# BeautyGlow Giai đoạn 6 — Hardening và Backup/Restore

Ngày thực hiện: 21/10/2026 (UTC+7). **PASS trong phạm vi bài thực hành** sau khi sửa lỗi verifier Unicode.
Người dùng đã phê duyệt commit/push riêng các file Giai đoạn 6 khi kiểm tra PASS. Giai đoạn 5
giữ nguyên; không chạy Bridge/Supervisor, không phát lại log hoặc email thử.

## Tiêu chí và kết quả

| Tiêu chí | Kết quả |
|---|---|
| Preflight Git/Compose/14 container/volume/network | PASS; hai file untracked cũ ngoài phạm vi giữ nguyên. |
| Ít nhất 4 biện pháp hardening | PASS kỹ thuật; xem các lớp bảo vệ bên dưới. |
| Non-root cho dịch vụ phù hợp | PASS; Blackbox HTTP chuyển 65534:65534; MySQL thực tế 999; Prometheus/Alertmanager/Alloy/Loki/Grafana/exporters phù hợp đã non-root. |
| MySQL không publish host port | PASS; chỉ network database nội bộ. |
| Phân tách mạng/giới hạn truy cập | PASS; database internal; frontend/monitoring riêng; mọi cổng publish bind 127.0.0.1. |
| HTTP Security Headers | PASS trên `/`, `/wp-login.php` (200) và đường dẫn không tồn tại (404); nginx -t PASS. |
| `.env` và secrets | PASS phần đã duyệt: ACL riêng .env, protected secrets trên ext4; không đọc/in credential. |
| Backup DB/uploads, có checksum | PASS: 12 bảng, 412 dòng; 64 file uploads; xem checksum trong PHASE6-RESULTS.json. |
| Restore cô lập | PASS khôi phục nguyên trạng: row counts/data/DDL tương đương, 64/64 file giống byte; không ghi production. |
| Tài khoản DB chỉ đọc | PASS trong DB thử: SELECT COUNT(*) trả 44; CREATE TABLE bị từ chối ERROR 1142, bảng không được tạo. Không đổi tài khoản production. |
| Website/MySQL/monitoring/logging sau thay đổi | PASS: 14 container hoạt động, 6/6 targets UP, 4 alert rules health=ok; không gửi email thử lại. |
| Toàn vẹn media nguồn | PASS: 14/14 ảnh gốc tồn tại; attachment32 có trong nguồn/backup/restore, checksum trùng; URL Unicode encode đúng trả HTTP200. |
| Nghiệm thu và Git | PASS kỹ thuật; chỉ commit/push allowlist Giai đoạn6 sau kiểm tra staged content/remote, loại trừ secret, backup và file không liên quan. |

Bằng chứng kỹ thuật đã lọc: `PHASE6-RESULTS.json`, `PHASE6-WINDOWS-ACL.json`.
Không chứa SQL dump, đường dẫn/tên media cá nhân, email, App Password hay raw logs.
Metadata chi tiết về media thiếu chỉ nằm trong backup bảo vệ ngoài Git.

## Các lớp bảo vệ đã kiểm chứng

1. MySQL/WordPress không mở cổng host; database network `internal: true`.
2. Cổng web/quản trị/monitoring publish chỉ loopback; Loki/Alloy/Alertmanager không
   mở cổng host. Không đổi monitoring thành internal vì Gmail cần SMTP egress.
3. Tất cả 14 container có no-new-privileges; không privileged/host network/socket
   Docker mount. PID/cgroup host của exporter là ngoại lệ quan sát được ghi rõ.
4. Monitoring phù hợp chạy non-root, read-only rootfs và cap_drop ALL. Blackbox
   chỉ probe HTTP/port9115 nên đã chuyển non-root, kiểm tra PID thực và probe_success=1.
5. Nginx có nosniff, SAMEORIGIN, strict-origin-when-cross-origin và Permissions-Policy;
   `always` áp dụng cả 404; server_tokens off. Chưa triển khai HTTPS/HSTS cho local HTTP.
6. WordPress DISALLOW_FILE_EDIT=true đã có; plugin/config bind read-only trong container.
7. Runtime secrets và Bridge/Policy/Supervisor ở ext4 được bảo vệ; .env Windows đã
   được duyệt riêng và siết ACL, bỏ Users ghi rộng. Không đưa secret vào Git.

Ngoại lệ root giữ nguyên để tránh gián đoạn: Nginx/Apache master và entrypoint
WordPress/phpMyAdmin dùng root để bind port80/chuẩn bị filesystem, worker chạy
theo image. MySQL entrypoint chuẩn bị quyền nhưng mysqld thực tế UID999. cAdvisor
hiện chạy root để quan sát host cgroups/sys; không privileged, không Docker socket,
read-only và drop ALL capabilities. Không tuyên bố root là bắt buộc cho mọi cấu
hình thay thế; chuyển kiến trúc/image và các quyền này cần đánh giá riêng.

Thay đổi production: chỉ ACL `.env` đã duyệt và recreate Blackbox Exporter với UID
65534. 13 container khác giữ ID/StartedAt/RestartCount; không sửa database/uploads.
Không restart/recreate WordPress, MySQL, Nginx hoặc các service Logging/Alerting.

## Backup nhất quán trong phạm vi bài thực hành

Backup: `/var/lib/beautyglow-backups/20261020T210409Z` (UTC; 04:04 ngày21 UTC+7).
Parent/run directory root:root 0700, các file backup/manifest/checksum 0600.

- Database `beautyglow_catalog`: 12 bảng InnoDB, không view/routine/event/trigger
  trong lần preflight. Dùng mysqldump --single-transaction --quick --skip-lock-tables,
  --no-tablespaces --set-gtid-purged=OFF --hex-blob; không dump mysql.user/production grants.
- Chụp uploads và hash từng file; hai logical dump có checksum giống nhau bao
  quanh lúc chụp files, inventory uploads trước/sau giống nhau. Nếu DB/files đổi,
  script dừng và không chấp nhận backup, không tự retry trên nguồn đang ghi.
- SQL 1.512.191 byte; archive uploads 2.548.230 byte; nội dung uploads 2.549.215 byte.
- `database.sql`, `uploads.tar.gz`, `SHA256SUMS`, `manifest.json`, `restore-result.json`
  nằm ngoài repository. Không copy chúng vào evidence/Git.

Đây là snapshot vận hành kiểm chứng được khi nguồn ổn định, không phải transaction
atomic xuyên filesystem/MySQL hoặc cam kết chống DDL đồng thời. Không triển khai
maintenance mode, khóa ghi website hoặc backup production vật lý. Khi nguồn có
ghi liên tục, cần phê duyệt phương án quiesce/snapshot khác; không tự dừng site.

## Restore cô lập và bằng chứng SELECT-only

`compose.restore-test.yaml` chỉ tạo project `beautyglow-restore-test`, dùng chính
image ID MySQL đang có (`--pull never`), network internal riêng, không publish port
và volume `beautyglow-restore-test_mysql_data`; không mount volume production.
Root password của DB thử được sinh riêng vào file ngoài Git; không tái dùng mật
khẩu production. Giới hạn 768MiB/1CPU; không restart tự động, tắt binlog/general log.

Uploads được giải nén vào `/var/lib/beautyglow-restore-tests/20261020T210409Z/files`,
tách hẳn uploads thật. Tar data filter và kiểm tra symlink/type chống đường dẫn
thoát thư mục. Mọi file được đối chiếu checksum/kích thước với inventory nguồn.

Đối chiếu 12 bảng/412 dòng. Dump sau restore có thêm cú pháp column
`CHARACTER SET utf8mb4` trước `COLLATE utf8mb4_*`; đây là cú pháp dư tương đương,
không phải dữ liệu mất. Verifier chỉ chuẩn hóa đúng token này trên dòng định nghĩa
column; mọi INSERT và phần SQL khác phải trùng. Có test đảm bảo không chuẩn hóa
nội dung row. Không tuyên bố raw SQL dump giống byte; checksum backup gốc giữ nguyên.

Tài khoản `beautyglow_audit_ro` chỉ tạo trong DB thử, GRANT SELECT trên
beautyglow_catalog.*. SELECT COUNT(*) trên bảng posts trả 44; CREATE TABLE
phase6_write_must_be_denied bị từ chối ERROR1142 và xác minh bảng không tồn tại.
Không thử ghi bằng tài khoản production. Container thử đã stop; network/volume/
uploads/credential thử được giữ lại, không xóa theo giới hạn người dùng.

Đính chính lỗi kiểm chứng trước: MySQL client không khai báo utf8mb4 nên ký tự
emoji 💯 trong tên file attachment32 bị đổi thành `?`; đường dẫn sai gây báo thiếu
và HTTP404 giả. File gốc 61.176 byte vẫn tồn tại, có trong archive/manifest/restore,
SHA-256 đều trùng. Đọc utf8mb4 và encode URL đúng trả HTTP200. Không mất dữ liệu,
không thay ảnh hoặc sửa WordPress/MySQL để giải quyết lỗi này.

Script hiện khai báo --default-character-set=utf8mb4 ở mọi mysql/mysqldump,
giải mã UTF-8 strict; kiểm đủ 14/14 ảnh gốc, không chỉ source/restore cùng thiếu.
12 test offline PASS, gồm query giữ nguyên emoji và archive/inventory roundtrip
Unicode. Báo cáo PENDING_MEDIA/13-of-14 trước đây đã được thay bằng kết quả đúng.
Backup gốc và checksum không đổi; chỉ xác minh lại bản restore hiện có rồi stop.

## Thực hiện lại sau này

Mọi live drill cần phê duyệt; không chạy tự động từ hướng dẫn này. Không chạy lại
`--execute` khi namespace thử đã có: script từ chối để tránh ghi đè. Không dùng
down/rm/prune/xóa volume để vượt guard. Chọn phương án drill mới trong bước riêng.

Code đã kiểm và sao chép root-owned vào `/opt/beautyglow-maintenance`, thư mục0750,
script0500, Compose/dummy env0440; hash khớp nguồn lúc triển khai. Không chạy code
root tùy ý từ Windows workspace còn quyền ghi rộng.

```sh
# Offline, user thường không có quyền socket:
python3 -I -B -m unittest discover -s tests/phase6 -p 'test_*.py' -v
# Chỉ xem kế hoạch; không Docker/ghi backup:
python3 -I -B scripts/beautyglow-backup-restore.py
```

`--verify-existing-restore <backup-directory>` yêu cầu --execute/root và phê duyệt;
chỉ start bản thử đã dừng, kiểm checksum/parity/grants rồi stop, không dump/import
production lần nữa. Không gọi đây là restore production. File debug/SQL đối chiếu
nếu có cũng thuộc backup bảo vệ, không đưa vào Git.

## Rollback và giới hạn còn lại

- ACL `.env`: bản SDDL trước thay đổi ở `backups/phase6-env-acl-20261021T040315.json`
  (gitignored). Khi được duyệt, đọc JSON, Get-Acl .env, gọi
  SetSecurityDescriptorSddlForm(sddl) rồi Set-Acl đúng file; không đổi thư mục cha.
  Operator hiện có Read/Write, SYSTEM/Administrators FullControl, không kế thừa.
  Đã kiểm Compose Ubuntu vẫn đọc được .env; không đọc nội dung để làm bằng chứng.
- Blackbox: bỏ duy nhất user65534 vừa thêm rồi recreate riêng blackbox-exporter
  bằng up --no-deps --pull never khi được duyệt; không whole-project up/down.
- Backup/restore: production không bị ghi nên không cần rollback dữ liệu. Test
  MySQL đã dừng; tuyệt đối không import database.sql vào MySQL hiện tại nếu chưa
  có phê duyệt phục hồi thật. Giữ volume/backup; cleanup cần bước riêng.
- Backup ở cùng host, chưa có offsite/encryption/DR. Host/root/Docker admin vẫn
  có thể đọc backup; không có đảm bảo chống mất cả máy. Windows ACL các config
  workspace khác còn rộng; chỉ .env được duyệt siết. Không tự đổi ACL hoặc remount
  Nginx vì có nguy cơ gián đoạn. HTTPS/HSTS và hardening bổ sung ngoài scope tối thiểu.

Tham khảo: [MySQL consistent dumps](https://dev.mysql.com/doc/refman/8.0/en/mysqldump.html),
[Nginx headers](https://nginx.org/en/docs/http/ngx_http_headers_module.html).

## Phạm vi Git Giai đoạn 6

Chỉ 8 file: `compose.yaml`, `compose.restore-test.yaml`,
`scripts/beautyglow-backup-restore.py`, `scripts/verify-phase6.py`,
`tests/phase6/test_restore_safety.py`, `docs/PHASE6-HARDENING-BACKUP.md`,
`docs/PHASE6-RESULTS.json`, `docs/PHASE6-WINDOWS-ACL.json`.
Không stage `.env`, credential, SQL/archive backup, manifest uploads, ACL backup,
state /opt hoặc /var/lib, `prometheus.batch43.yml`, ảnh monitoring chưa rà soát.
Giữ nguyên Logging/Alerting đã PASS. Không amend/reset/force-push.
