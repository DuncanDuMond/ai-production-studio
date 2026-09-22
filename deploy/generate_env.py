"""Generate a local .env once; never contact a service or print its secrets."""

import argparse
import base64
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    for key in [
        "STUDIO_API_TOKEN", "LANGFUSE_POSTGRES_PASSWORD", "LANGFUSE_CLICKHOUSE_PASSWORD",
        "LANGFUSE_REDIS_PASSWORD", "LANGFUSE_SALT", "LANGFUSE_NEXTAUTH_SECRET",
        "LANGFUSE_ENCRYPTION_KEY", "LANGFUSE_S3_SECRET_KEY",
    ]:
        values[key] = secrets.token_hex(32)
    values["PHOENIX_SECRET"] = "ps1" + secrets.token_hex(32)
    values["PHOENIX_ADMIN_PASSWORD"] = "Aa1!" + secrets.token_hex(24)
    values["LANGFUSE_INIT_USER_PASSWORD"] = "Aa1!" + secrets.token_hex(24)
    values["LANGFUSE_INIT_USER_EMAIL"] = "admin@localhost.invalid"
    values["LANGFUSE_S3_ACCESS_KEY"] = "studio" + secrets.token_hex(8)
    values["LITELLM_MASTER_KEY"] = "sk-studio-" + secrets.token_hex(32)
    values["STUDIO_LITELLM_API_KEY"] = values["LITELLM_MASTER_KEY"]
    values["LANGFUSE_PUBLIC_KEY"] = "pk-lf-" + secrets.token_hex(16)
    values["LANGFUSE_SECRET_KEY"] = "sk-lf-" + secrets.token_hex(32)
    credentials = values["LANGFUSE_PUBLIC_KEY"] + ":" + values["LANGFUSE_SECRET_KEY"]
    values["LANGFUSE_BASIC_AUTH"] = base64.b64encode(credentials.encode()).decode("ascii")
    content = "# Private, local-only credentials. Do not commit or log.\n"
    content += "".join(f"{key}={value}\n" for key, value in values.items())
    try:
        descriptor = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        parser.exit(1, "Refusing to replace an environment file that already exists.\n")
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    print("Created private environment file. Provider and Phoenix API keys remain unset.")
    if os.name == "nt":
        print("Windows: review the file ACL; POSIX mode 0600 is not an ACL guarantee.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
