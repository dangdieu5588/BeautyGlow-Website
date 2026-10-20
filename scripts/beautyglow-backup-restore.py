"""Explicit backup/isolated restore drill; no production writes or account changes.

Requires authorized Ubuntu root/Docker access. Run --execute after preflight.
Backups/credentials stay outside Git. Stops only its new test container, preserves
all volumes, and refuses to reuse an existing test namespace. No down/rm/prune.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "beautyglow-website-mysql-1"
PROJECT = "beautyglow-restore-test"
DATABASE = "beautyglow_catalog"


def command(args, **kwargs):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=kwargs.pop("timeout", 120), **kwargs)
    if result.returncode:
        raise RuntimeError("Command failed; details suppressed to protect data: " + args[0])
    return result.stdout


def inspect(kind, name):
    return json.loads(command(["docker", kind, "inspect", name]))[0]


def source_client(program, extra=()):
    assert program in {"mysql", "mysqldump"}
    # The password expands only inside the source container, never in argv/output.
    script = 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec ' + program + ' --default-character-set=utf8mb4 --user=root "$@"'
    return ["docker", "exec", "-i", SOURCE, "sh", "-ec", script, "sh", *extra]


def query(sql, container=None, readonly=False):
    if container is None:
        if not re.fullmatch(r"\s*(?:SELECT|SHOW)\b[^;]*;?\s*", sql, re.IGNORECASE | re.DOTALL):
            raise RuntimeError("Production query must be a single read-only SELECT/SHOW")
        if re.search(r"\b(?:INTO|OUTFILE|DUMPFILE|FOR\s+UPDATE|GET_LOCK|SLEEP)\b", sql, re.IGNORECASE):
            raise RuntimeError("Unsafe production SELECT rejected")
        args = source_client("mysql", ["--batch", "--skip-column-names", DATABASE])
    else:
        password_file = "mysql-readonly-password" if readonly else "mysql-root-password"
        user = "beautyglow_audit_ro" if readonly else "root"
        script = ('MYSQL_PWD="$(cat /run/secrets/' + password_file + ')" '
                  'exec mysql --default-character-set=utf8mb4 --batch --skip-column-names --user=' + user + ' ' + DATABASE)
        args = ["docker", "exec", "-i", container, "sh", "-ec", script]
    return command(args, input=sql.encode()).decode().strip()


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise RuntimeError("Unexpected uploads entry; stopped before backup")
        if path.is_file():
            result[str(path.relative_to(directory))] = {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    return result


def stream_dump(args, destination=None):
    digest = hashlib.sha256()
    # stderr goes to a private temporary file to avoid pipe deadlocks or leaks.
    import tempfile
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(["/usr/bin/timeout", "--signal=TERM", "--kill-after=5", "120", *args],
                                   stdout=subprocess.PIPE, stderr=errors)
        try:
            for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
                digest.update(chunk)
                if destination:
                    destination.write(chunk)
            if process.wait(timeout=120):
                raise RuntimeError("Dump failed; diagnostic content suppressed")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()
    return digest.hexdigest()


def protected_directory(path):
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or stat.S_IMODE(s.st_mode) != 0o700:
        raise RuntimeError("Unsafe backup directory")


def normalize_dump(data):
    """Normalize only redundant column charset syntax, never INSERT row content.

    COLLATE utf8mb4_* already implies CHARACTER SET utf8mb4. MySQL can add the
    explicit charset after restore; all other DDL and every data byte must match.
    """
    return b"".join(re.sub(rb" CHARACTER SET utf8mb4(?= COLLATE utf8mb4_)", b"", line)
                    if re.match(rb"^\s+`[A-Za-z0-9_]+` ", line) else line
                    for line in data.splitlines(keepends=True))


def verify_restored(container, backup, restore, uploads, tables, table_counts, upload_before):
    sql_path = backup / "database.sql"
    archive = backup / "uploads.tar.gz"
    restored_uploads = restore / "files"
    sql_sha = hashlib.sha256(sql_path.read_bytes()).hexdigest()
    tar_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    dump_options = ["--default-character-set=utf8mb4", "--single-transaction", "--quick", "--skip-lock-tables",
                    "--set-gtid-purged=OFF", "--no-tablespaces", "--skip-comments",
                    "--hex-blob", "--order-by-primary", "--skip-add-locks",
                    "--skip-disable-keys", "--routines", "--events", DATABASE]
    restored = {t: int(query("SELECT COUNT(*) FROM `" + t + "`;", container["Id"])) for t in tables}
    assert restored == table_counts
    dump_script = 'MYSQL_PWD="$(cat /run/secrets/mysql-root-password)" exec mysqldump --default-character-set=utf8mb4 --user=root "$@"'
    with (backup / "restored-verification.sql").open("wb") as output:
        restored_sha = stream_dump(["docker", "exec", "-i", container["Id"], "sh", "-ec", dump_script, "sh", *dump_options], output)
    original_sql = sql_path.read_bytes()
    restored_sql = (backup / "restored-verification.sql").read_bytes()
    assert normalize_dump(original_sql) == normalize_dump(restored_sql), "Restored SQL differs beyond redundant charset syntax"
    posts = next(t for t in tables if t.endswith("_posts"))
    postmeta = posts[:-5] + "postmeta"
    attachments = query("SELECT meta_value FROM `" + postmeta + "` WHERE meta_key='_wp_attached_file';", container["Id"]).splitlines()
    assert all(not Path(a).is_absolute() and ".." not in Path(a).parts for a in attachments)
    source_missing = {a for a in attachments if not (uploads / a).is_file()}
    restored_missing = {a for a in attachments if not (restored_uploads / "uploads" / a).is_file()}
    assert not source_missing and not restored_missing, "Missing attachment files require review"
    assert inventory(restored_uploads / "uploads") == upload_before
    ro_password = (restore / "mysql-readonly-password").read_text()
    assert re.fullmatch(r"[a-f0-9]{48}", ro_password)
    if query("SELECT COUNT(*) FROM mysql.user WHERE user='beautyglow_audit_ro';", container["Id"]) == "0":
        query("CREATE USER 'beautyglow_audit_ro'@'localhost' IDENTIFIED BY '" + ro_password + "'; GRANT SELECT ON " + DATABASE + ".* TO 'beautyglow_audit_ro'@'localhost';", container["Id"])
    grants = query("SHOW GRANTS FOR 'beautyglow_audit_ro'@'localhost';", container["Id"]).splitlines()
    assert len(grants) == 2 and any(g.startswith("GRANT SELECT ON `" + DATABASE + "`.* TO ") for g in grants)
    assert all(g.startswith(("GRANT USAGE ON ", "GRANT SELECT ON ")) for g in grants)
    selected_count = int(query("SELECT COUNT(*) FROM `" + posts + "`;", container["Id"], readonly=True))
    denied_script = 'MYSQL_PWD="$(cat /run/secrets/mysql-readonly-password)" exec mysql --default-character-set=utf8mb4 --user=beautyglow_audit_ro ' + DATABASE
    denied = subprocess.run(["docker", "exec", "-i", container["Id"], "sh", "-ec", denied_script],
                            input=b"CREATE TABLE phase6_write_must_be_denied (id INT);", capture_output=True, timeout=15)
    assert denied.returncode != 0 and b"ERROR 1142" in denied.stderr
    assert query("SHOW TABLES LIKE 'phase6_write_must_be_denied';", container["Id"]) == ""
    summary = {"status": "PASS", "backup_directory": str(backup), "restore_directory": str(restore),
               "database_tables": len(tables), "database_rows": sum(restored.values()),
               "database_bytes": sql_path.stat().st_size, "database_sha256": sql_sha,
               "uploads_files": len(upload_before), "uploads_bytes": sum(v["bytes"] for v in upload_before.values()),
               "uploads_archive_bytes": archive.stat().st_size, "uploads_sha256": tar_sha,
               "restored_sql_raw_identical": restored_sha == sql_sha, "restored_sql_semantically_identical": True, "restored_uploads_identical": True,
               "attachment_files_verified": len(attachments) - len(source_missing), "preexisting_missing_attachment_files": len(source_missing), "readonly_select_count": selected_count,
               "readonly_create_denied_mysql_error": 1142, "readonly_account_scope": "isolated restore only",
               "production_accounts_changed": False, "production_data_written": False,
               "restore_container": container["Id"], "restore_volume_preserved": True}
    (backup / "restore-result.json").write_text(json.dumps(summary, indent=2))
    print("RESTORE_PASS", json.dumps(summary), flush=True)


def verify_existing_restore(backup):
    backup = backup.resolve(strict=True)
    if backup.parent != Path("/var/lib/beautyglow-backups") or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", backup.name):
        raise RuntimeError("Unexpected backup path")
    for line in (backup / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split()
        assert name in {"database.sql", "uploads.tar.gz"}
        assert hashlib.sha256((backup / name).read_bytes()).hexdigest() == expected
    restore = Path("/var/lib/beautyglow-restore-tests") / backup.name
    manifest = json.loads((backup / "manifest.json").read_text())
    volume = inspect("volume", "beautyglow-website_wordpress_data")
    uploads = Path(volume["Mountpoint"]) / "wp-content/uploads"
    assert inventory(uploads) == manifest["uploads"], "Source uploads changed since backup; review parity"
    container = inspect("container", PROJECT + "-mysql-1")
    assert container["Config"]["Labels"]["com.docker.compose.project"] == PROJECT
    assert not container["State"]["Running"] and not container["HostConfig"]["PortBindings"]
    assert set(container["NetworkSettings"]["Networks"]) == {PROJECT + "_isolated"}
    assert {m["Name"] for m in container["Mounts"] if m["Type"] == "volume"} == {PROJECT + "_mysql_data"}
    assert inspect("network", PROJECT + "_isolated")["Internal"]
    assert container["Image"] == inspect("container", SOURCE)["Image"]
    try:
        command(["docker", "start", container["Id"]])
        deadline = time.monotonic() + 90
        while True:
            try:
                command(["docker", "exec", container["Id"], "sh", "-ec",
                         'MYSQL_PWD="$(cat /run/secrets/mysql-root-password)" exec mysql --default-character-set=utf8mb4 --protocol=TCP --host=127.0.0.1 --user=root --execute="SELECT 1"'])
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Isolated verifier readiness timeout")
                time.sleep(2)
        verify_restored(container, backup, restore, uploads, list(manifest["tables"]), manifest["tables"], manifest["uploads"])
    finally:
        command(["docker", "stop", "--time", "30", container["Id"]], timeout=45)
        print("ISOLATED_VERIFICATION_STOPPED; all volumes retained", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--verify-existing-restore", type=Path, help="Verify an already restored, stopped isolated test; no production backup/import")
    args = parser.parse_args()
    if not args.execute:
        print("Plan: read-only production dump/uploads -> protected backup -> isolated restore -> stop test; no volume deletion.")
        return
    if os.geteuid() != 0:
        raise SystemExit("Authorized Ubuntu root is required for protected backup paths.")
    os.umask(0o077)
    if args.verify_existing_restore:
        verify_existing_restore(args.verify_existing_restore)
        return
    existing = command(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT]).strip()
    if existing:
        raise RuntimeError("Restore project already exists; refuse reuse")
    for kind, name in [("volume", PROJECT + "_mysql_data"), ("network", PROJECT + "_isolated")]:
        check = subprocess.run(["docker", kind, "inspect", name], capture_output=True)
        if check.returncode == 0:
            raise RuntimeError("Restore resource already exists; refuse reuse")
    source = inspect("container", SOURCE)
    assert source["Config"]["Labels"]["com.docker.compose.project"] == "beautyglow-website"
    assert not source["HostConfig"]["PortBindings"]
    assert source["State"]["Running"]
    assert inspect("network", "beautyglow-website_database")["Internal"]
    volume = inspect("volume", "beautyglow-website_wordpress_data")
    uploads = Path(volume["Mountpoint"]) / "wp-content/uploads"
    if not uploads.is_dir() or uploads.is_symlink():
        raise RuntimeError("Uploads directory unavailable")
    engines = query("SELECT DISTINCT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE();").splitlines()
    if engines != ["InnoDB"]:
        raise RuntimeError("Non-InnoDB tables/views: consistent online backup needs review")
    tables = query("SHOW TABLES;").splitlines()
    assert tables and all(re.fullmatch(r"[A-Za-z0-9_]+", t) for t in tables)
    table_counts = {t: int(query("SELECT COUNT(*) FROM `" + t + "`;")) for t in tables}
    upload_before = inventory(uploads)
    assert upload_before, "Uploads are empty; review expected backup scope"
    base = Path("/var/lib/beautyglow-backups")
    protected_directory(base)
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = base / run_id
    backup.mkdir(mode=0o700)
    dump_options = ["--default-character-set=utf8mb4", "--single-transaction", "--quick", "--skip-lock-tables",
                    "--set-gtid-purged=OFF", "--no-tablespaces", "--skip-comments",
                    "--hex-blob", "--order-by-primary", "--skip-add-locks",
                    "--skip-disable-keys", "--routines", "--events", DATABASE]
    sql_path = backup / "database.sql"
    with sql_path.open("xb") as output:
        sql_sha = stream_dump(source_client("mysqldump", dump_options), output)
        output.flush()
        os.fsync(output.fileno())
    archive = backup / "uploads.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(uploads, arcname="uploads")
    if inventory(uploads) != upload_before:
        raise RuntimeError("Uploads changed during capture; backup not accepted")
    # Two matching logical snapshots bracket the file capture. Reject source
    # activity instead of pretending independent DB/files snapshots are atomic.
    if stream_dump(source_client("mysqldump", dump_options)) != sql_sha:
        raise RuntimeError("Database changed during capture; backup not accepted")
    assert sql_path.stat().st_size > 0 and archive.stat().st_size > 0
    tar_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    (backup / "SHA256SUMS").write_text(sql_sha + "  database.sql\n" + tar_sha + "  uploads.tar.gz\n")
    (backup / "manifest.json").write_text(json.dumps({"tables": table_counts, "uploads": upload_before}, indent=2))
    print("BACKUP_PASS", str(backup), "tables", len(tables), "upload_files", len(upload_before), flush=True)

    restore_base = Path("/var/lib/beautyglow-restore-tests")
    protected_directory(restore_base)
    restore = restore_base / run_id
    restore.mkdir(mode=0o700)
    for name in ["mysql-root-password", "mysql-readonly-password"]:
        with (restore / name).open("x") as f:
            f.write(secrets.token_hex(24))
        (restore / name).chmod(0o400)
    restored_uploads = restore / "files"
    restored_uploads.mkdir(mode=0o700)
    with tarfile.open(archive) as tar:
        tar.extractall(restored_uploads, filter="data")
    assert inventory(restored_uploads / "uploads") == upload_before
    env = os.environ.copy()
    env["BEAUTYGLOW_RESTORE_IMAGE"] = source["Image"]
    env["BEAUTYGLOW_RESTORE_SECRETS_DIR"] = str(restore)
    compose = ["docker", "compose", "--project-directory", str(ROOT), "--env-file", str(ROOT / ".env.example"),
               "-f", str(ROOT / "compose.restore-test.yaml")]
    command(compose + ["config", "--quiet"], env=env)
    container = None
    try:
        command(compose + ["up", "-d", "--no-deps", "--pull", "never", "mysql"], env=env)
        container = inspect("container", PROJECT + "-mysql-1")
        assert container["Config"]["Labels"]["com.docker.compose.project"] == PROJECT
        assert not container["HostConfig"]["PortBindings"]
        assert set(container["NetworkSettings"]["Networks"]) == {PROJECT + "_isolated"}
        assert inspect("network", PROJECT + "_isolated")["Internal"]
        assert {m["Name"] for m in container["Mounts"] if m["Type"] == "volume"} == {PROJECT + "_mysql_data"}
        deadline = time.monotonic() + 150
        while True:
            try:
                command(["docker", "exec", container["Id"], "sh", "-ec",
                         'MYSQL_PWD="$(cat /run/secrets/mysql-root-password)" '
                         'exec mysql --default-character-set=utf8mb4 --protocol=TCP --host=127.0.0.1 --user=root '
                         '--database=' + DATABASE + ' --execute="SELECT 1"'])
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Isolated MySQL did not become ready")
                time.sleep(2)
        print("ISOLATED_MYSQL_READY; no production mounts/networks/ports", flush=True)
        with sql_path.open("rb") as source_sql:
            args = ["docker", "exec", "-i", container["Id"], "sh", "-ec",
                    'MYSQL_PWD="$(cat /run/secrets/mysql-root-password)" exec mysql --default-character-set=utf8mb4 --user=root ' + DATABASE]
            result = subprocess.run(args, stdin=source_sql, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, timeout=120)
            if result.returncode:
                raise RuntimeError("Isolated restore failed; details suppressed")
        verify_restored(container, backup, restore, uploads, tables, table_counts, upload_before)
    finally:
        # Find only our unique test container even if create/start failed midway.
        if container is None:
            found = command(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + PROJECT]).decode().split()
            if len(found) == 1:
                container = inspect("container", found[0])
        if container is not None:
            assert container["Config"]["Labels"]["com.docker.compose.project"] == PROJECT
            command(["docker", "stop", "--time", "30", container["Id"]], timeout=45)
            print("TEST_CONTAINER_STOPPED; database, uploads, backups, volume/network retained", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never include SQL, subprocess stderr, credentials or row contents.
        print("FAIL", type(error).__name__, ": protected diagnostic review required; no production rollback attempted.", flush=True)
        raise SystemExit(1)
