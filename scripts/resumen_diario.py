"""Resumen diario de inventario: alertas nuevas + resumen con IA.

Guarda el resultado en data/resumenes/AAAA-MM-DD.md y, si hay SMTP configurado en el .env
(SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, RESUMEN_PARA), lo envía por email.

Uso (desde la carpeta del proyecto):
    .venv/bin/python -m scripts.resumen_diario            # alertas + IA
    .venv/bin/python -m scripts.resumen_diario --sin-ia   # solo alertas
Para programarlo, ver README (cron).
"""

import argparse
import smtplib
import ssl
import sys
from datetime import date
from email.message import EmailMessage

from app import ai, alerts, analytics, config, data


def build(con_ia: bool) -> str:
    s = config.load_settings()
    snap = data.get_snapshot(force=True)
    metrics = analytics.compute(snap, s)
    al = [a for a in alerts.evaluate(metrics, snap, s) if not a["acked"]]
    urg = [a for a in al if a["severity"] in ("critica", "alta")]
    lines = [f"# Inventario · {date.today().strftime('%d/%m/%Y')}", ""]
    for w in snap["warnings"]:
        lines.append(f"> ⚠ {w}")
    lines += ["", f"**{len(urg)} alertas urgentes** ({sum(a['severity'] == 'critica' for a in al)} críticas) de {len(al)} activas.", ""]
    if urg:
        lines += ["| Severidad | Ref. | Alerta | Acción |", "|---|---|---|---|"]
        for a in urg[:40]:
            lines.append(f"| {a['severity']} | {a['ref']} | {a['title']} | {a['action']} |")
    if con_ia and ai.ai_available():
        try:
            lines += ["", "---", "", ai.summary()["text"]]
        except Exception as e:  # el resumen de alertas sale igualmente
            lines += ["", f"_(No se pudo generar el resumen con IA: {type(e).__name__}: {e})_"]
    return "\n".join(lines)


def send(md_text: str):
    host, to = config.env("SMTP_HOST"), config.env("RESUMEN_PARA")
    if not host or not to:
        return False
    msg = EmailMessage()
    msg["Subject"] = f"Inventario · resumen {date.today().strftime('%d/%m/%Y')}"
    msg["From"] = config.env("SMTP_FROM", config.env("SMTP_USER"))
    msg["To"] = to
    msg.set_content(md_text)
    with smtplib.SMTP(host, int(config.env("SMTP_PORT", "587"))) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if config.env("SMTP_USER"):
            smtp.login(config.env("SMTP_USER"), config.env("SMTP_PASSWORD", ""))
        smtp.send_message(msg)
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sin-ia", action="store_true")
    args = p.parse_args()
    text = build(con_ia=not args.sin_ia)
    out_dir = config.DATA_DIR / "resumenes"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{date.today().isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    sent = send(text)
    print(f"Guardado en {path}" + (" · enviado por email" if sent else ""))


if __name__ == "__main__":
    sys.exit(main())
