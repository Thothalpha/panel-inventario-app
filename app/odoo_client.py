"""Cliente Odoo de SOLO LECTURA vía XML-RPC (compatible con Odoo 12–19).

Credenciales por variables de entorno:
  ODOO_URL       p.ej. https://miempresa.odoo.com
  ODOO_DB        nombre de la base de datos
  ODOO_USER      login del usuario (idealmente un usuario con permisos solo de lectura)
  ODOO_API_KEY   API key del usuario (Preferencias > Seguridad de la cuenta > Nueva API key)
"""

import os
import xmlrpc.client

# Únicos métodos permitidos: cualquier otro (create, write, unlink, action_*, etc.) se rechaza.
READ_METHODS = frozenset({
    "search", "search_read", "search_count", "read", "read_group",
    "fields_get", "name_search", "name_get", "check_access_rights",
})


def load_dotenv(path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")):
    """Carga KEY=VALUE desde .env sin sobrescribir variables ya definidas en el entorno."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(key.strip(), value)


load_dotenv()


class ReadOnlyViolation(Exception):
    pass


TIMEOUT = 60  # segundos; XML-RPC no tiene límite por defecto y una llamada colgada bloquearía la app


class _TimeoutTransport(xmlrpc.client.Transport):
    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = TIMEOUT
        return conn


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = TIMEOUT
        return conn


def _proxy(url):
    transport = _TimeoutSafeTransport() if url.startswith("https") else _TimeoutTransport()
    return xmlrpc.client.ServerProxy(url, allow_none=True, transport=transport)


def detect_db(url: str) -> str:
    """Nombre de la base de datos cuando el usuario no lo indica."""
    url = url.rstrip("/")
    try:
        dbs = _proxy(f"{url}/xmlrpc/2/db").list()
        if len(dbs) == 1:
            return dbs[0]
        if len(dbs) > 1:
            raise ValueError("Este Odoo tiene varias bases de datos: indica cuál es la tuya")
    except xmlrpc.client.Fault:
        pass  # listado desactivado (lo normal en Odoo Online y Odoo.sh)
    host = url.split("://", 1)[-1].split("/", 1)[0]
    if host.endswith(".odoo.com"):
        # Solo es una suposición: vale para Odoo Online, no para Odoo.sh (allí la BD tiene otro nombre).
        # Si no es correcta, la autenticación lo detecta y pide escribirla.
        return host.split(".", 1)[0]
    raise ValueError("No se pudo detectar la base de datos. " + DB_HINT)


DB_HINT = ("Escribe su nombre exacto en el campo «Base de datos». En Odoo.sh es el nombre de la base de datos "
           "de producción (página del proyecto en odoo.sh, rama de producción).")


def fault_message(fault: xmlrpc.client.Fault) -> str:
    """Primera línea útil del error que devuelve Odoo (sin la traza de Python)."""
    lines = [ln.strip() for ln in str(fault.faultString or "").splitlines() if ln.strip()]
    useful = [ln for ln in lines if not ln.startswith(("Traceback", "File ", "^"))]
    return (useful[-1] if useful else str(fault.faultCode))[:300]


def is_db_fault(fault: xmlrpc.client.Fault) -> bool:
    s = str(fault.faultString or "").lower()
    return "database" in s and ("does not exist" in s or "not exist" in s or "doesn't exist" in s) \
        or "keyerror" in s and "db" in s


class OdooReadOnly:
    def __init__(self, url=None, db=None, user=None, api_key=None):
        self.url = (url or os.environ["ODOO_URL"]).rstrip("/")
        self.db = db or os.environ["ODOO_DB"]
        self.user = user or os.environ["ODOO_USER"]
        self.api_key = api_key or os.environ["ODOO_API_KEY"]
        self._common = _proxy(f"{self.url}/xmlrpc/2/common")
        self._models = _proxy(f"{self.url}/xmlrpc/2/object")
        self._uid = None

    def version(self):
        return self._common.version()

    @property
    def uid(self):
        if self._uid is None:
            try:
                uid = self._common.authenticate(self.db, self.user, self.api_key, {})
            except xmlrpc.client.Fault as f:
                if is_db_fault(f):
                    raise ValueError(f"La base de datos «{self.db}» no existe en este Odoo. " + DB_HINT) from f
                raise
            if not uid:
                raise PermissionError("Odoo rechaza el usuario o la API key (puede que la clave haya caducado)")
            self._uid = uid
        return self._uid

    def call(self, model, method, *args, **kwargs):
        if method not in READ_METHODS:
            raise ReadOnlyViolation(f"Método '{method}' bloqueado: este cliente es de solo lectura")
        return self._models.execute_kw(self.db, self.uid, self.api_key, model, method, list(args), kwargs)

    # Atajos
    def search_read(self, model, domain=None, fields=None, limit=None, offset=0, order=None):
        kw = {"fields": fields or [], "offset": offset}
        if limit:
            kw["limit"] = limit
        if order:
            kw["order"] = order
        return self.call(model, "search_read", domain or [], **kw)

    def search_read_all(self, model, domain=None, fields=None, order=None, batch=2000):
        """Pagina automáticamente para extraer todos los registros."""
        out, offset = [], 0
        while True:
            chunk = self.search_read(model, domain, fields, limit=batch, offset=offset, order=order or "id")
            out.extend(chunk)
            if len(chunk) < batch:
                return out
            offset += batch

    def search_count(self, model, domain=None):
        return self.call(model, "search_count", domain or [])

    def read_group(self, model, domain, fields, groupby, orderby=None, lazy=False):
        kw = {"lazy": lazy}
        if orderby:
            kw["orderby"] = orderby
        return self.call(model, "read_group", domain or [], fields, groupby, **kw)

    def fields_get(self, model, attributes=("string", "type", "relation", "store", "help")):
        return self.call(model, "fields_get", attributes=list(attributes))

    def list_models(self, name_filter=None):
        domain = [("transient", "=", False)]
        if name_filter:
            domain += ["|", ("model", "ilike", name_filter), ("name", "ilike", name_filter)]
        return self.search_read("ir.model", domain, ["model", "name"], order="model")
