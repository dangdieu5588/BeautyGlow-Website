# BeautyGlow — vận hành logging Giai đoạn 5

Trạng thái nghiệm thu ngày 20/10/2026, UTC+7. Chỉ project
`beautyglow-website`; không áp dụng lệnh vào Lab hoặc project khác.

## Kiến trúc và trạng thái

```text
Docker json-file logs (nginx/wordpress/mysql)
  → Docker Unix API → Bridge UID 474 (theo đợt được duyệt)
  → JSONL spool → Alloy UID 473 + nhóm đọc 475
  → Loki UID 10001, TSDB/filesystem/WAL → Grafana datasource proxy
```

Bridge dùng allowlist project/service và ID đầy đủ 64 ký tự; đọc API v1.51
không follow, kiểm tra identity/TTY, timestamp và redaction rồi ghi JSONL.
Checkpoint dùng journal pending, atomic replace và fsync; replay window 120 giây
khử trùng trong phạm vi đã thiết kế. Đây không phải cam kết exactly-once toàn tuyến.
Alloy chỉ mount đọc thư mục nginx/wordpress/mysql, không thấy checkpoint, không
có Docker socket. Alloy positions và Loki WAL/storage dùng hai volume riêng:
`beautyglow-logging-alloy-data`, `beautyglow-logging-loki-data`.

Bootstrap cố định `2026-10-19T10:13:25Z`; snapshot đã nghiệm thu:

| Service | stdout | stderr | Tổng |
|---|---:|---:|---:|
| nginx | 1.088 | 217 | 1.305 |
| wordpress | 2.630 | 10 | 2.640 |
| mysql | 8 | 24 | 32 |
| Tổng | 3.726 | 251 | 3.977 |

Cursor cả ba service: `2026-10-19T18:16:40Z`. Đây là snapshot lịch sử; Bridge
đang dừng nên cursor không tiếp tục theo giờ hiện tại. Các GET kiểm tra website
có thể tạo Docker access log mới, nhưng không tự đưa chúng vào spool.

## Khởi động và kiểm tra

Từ Windows, kiểm tra `wsl --list --running --quiet` trước. Nếu Ubuntu dừng,
xin phép riêng trước khi `wsl -d Ubuntu`; việc mở distro có thể tự khởi động
Docker và container có restart policy. Không dùng Docker Desktop thay Engine
Ubuntu. Distro dừng thì dừng kiểm tra, không giả định trạng thái cũ còn đúng.

Trong Ubuntu đang chạy, lệnh chỉ đọc:

```sh
docker version --format '{{.Server.Os}} {{.Server.Version}}'
docker ps -a --no-trunc --filter label=com.docker.compose.project=beautyglow-website
cd /mnt/d/BeautyGlow-Website
docker compose -p beautyglow-website --env-file .env config --quiet
```

Không in Compose đã interpolate, Env hoặc full inspect. Chỉ trích service,
ID, StartedAt, RestartCount, health và địa chỉ mạng cần thiết. Đối chiếu ID đầy
đủ 64 ký tự; 12 ký tự không đủ để so sánh checkpoint. Kỳ vọng 13 container;
healthcheck hiện có phải healthy. Dùng IP mạng monitoring hiện tại (không ghi
cứng IP): Loki GET :3100/ready, Alloy GET :12345/-/ready. Grafana
http://localhost:13000/api/health, Prometheus http://localhost:19090/-/ready.

Kiểm tra `/opt` và private spool dưới UID 474 bằng launcher hạ quyền, clear nhóm
phụ; cần phê duyệt launcher có quyền cao. Không gọi constructor Spool để kiểm tra:
nó có thể recovery và ghi. Chỉ đọc JSON, stat/hash, thử flock read-only; không tạo
lock marker, không truncate hoặc sửa pending.

## Chạy một đợt — chỉ khi được phê duyệt

Không thực hiện lệnh dưới đây như một phần kiểm tra. Trước mỗi lần chạy phải
đạt readiness, full source IDs, hash/mode /opt, policy, bootstrap/cursor, không
pending/checkpoint.tmp, lock rảnh, spool dưới 8 MiB, không Bridge/supervisor hoặc
UID 474 giữ nhóm socket khác. Đọc GID socket thực tế; 986 chỉ là giá trị đã ghi
nhận, không phải cấu hình cố định. Chạy duy nhất mã bảo vệ trên ext4 `/opt`.

| Đối tượng | Owner/mode |
|---|---|
| /opt/beautyglow-bridge | root:474 0750 |
| Bridge | 474:474 0500 |
| Supervisor | root:474 0550 |
| Policy | root:474 0440 |
| Spool root | 474:474 0700 |
| Service directories | 474:475 2750 |
| JSONL / checkpoint-lock | 0640 / 0600 |

Lệnh mẫu, thay `<GID_SOCKET_DA_XAC_MINH>` bằng số vừa kiểm tra:

```sh
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent \
 /usr/bin/setpriv --reuid=474 --regid=474 --groups=475,<GID_SOCKET_DA_XAC_MINH> --no-new-privs \
 /usr/bin/python3 -I -B /opt/beautyglow-bridge/beautyglow-log-supervisor.py \
 --run --bridge-sha256 f461bd7c6e9e56c8f3ae90c9fa3f6b3282dd810b956bc7f5bc98277f7e840814
```

Supervisor hiện tại hash `06d437d8c0bba8e8e05f4df4cf0f626b395a85fe5927da13baa5d025fa0b29db`.
Policy hash `fb0c75df818c03d6ef18d313eea8c17fe11756d768474946fc4b5ead6ac8478a`.
Supervisor và Bridge con đều Python isolated `-I -B`, environment sạch. Nhóm
socket chỉ cấp cho đợt này, không usermod/chmod socket. Giới hạn 300 poll,
120 giây/poll, khoảng nghỉ 1 giây, deadline làm việc 900 giây, spool 8 MiB.
Batch lớn hơn 120 giây cần thiết kế/test riêng, không tự tăng replay policy.

## Dừng và hậu kiểm

Supervisor tự dừng khi lag <120 giây, hết poll/time, lỗi, retry/rejection,
checkpoint không tiến, pending bất thường hoặc quota. CAUGHT_UP không có nghĩa
lag bằng 0. Khi cần dừng thủ công, xác định đúng PID supervisor trong đợt và
gửi SIGINT/SIGTERM; không pkill theo tên hoặc ép kill hàng loạt.
Supervisor TERM nhóm Bridge, đợi 5 giây, KILL nếu cần và reap. Cleanup có thể
kéo dài thêm khoảng 10 giây ngoài deadline làm việc. Kernel parent-death signal
bảo vệ Bridge con nếu supervisor chết; không bảo đảm kernel I/O treo vô hạn
hay tiến trình độc hại tự thoát session. Xem bảng exit code trong tài liệu supervisor.

Hậu kiểm bắt buộc: không còn Bridge/supervisor hoặc UID 474 giữ nhóm socket;
checkpoint/bootstrap, pending, JSONL delta, spool size; readiness và 13 container
ID/StartedAt/RestartCount trước-sau. Không tự chạy lại khi lỗi. Khi dừng toàn
môi trường, phải xin phép riêng, dừng Bridge trước, cho Alloy gửi và Loki flush
tự nhiên, kiểm tra đối chiếu rồi mới cân nhắc graceful stop riêng BeautyGlow.
Không down -v, prune, rm volume, flush thủ công hoặc terminate WSL để “sửa lỗi”.

## Dung lượng, retention và khôi phục

Quota Bridge tính tổng st_size regular files, gồm lock, journal/checkpoint tạm;
8.388.608 byte là ngưỡng dừng phòng ngừa, hard limit 16.777.216 byte. Không phải
quota block/inode filesystem. Bridge dự trù trước ghi; supervisor sampling chỉ
bổ sung giám sát. Không eviction/cleanup tự động. Theo dõi riêng dung lượng thật
của ext4 và volumes; Loki retention 168h không phải giới hạn đĩa chính xác.

| Tình huống | Xử lý an toàn |
|---|---|
| Exit 3 / spool gần đầy | Dừng, báo dung lượng; không nâng quota/xóa JSONL. Xin duyệt kế hoạch lưu trữ/cleanup có chứng cứ giao nhận và backup trước. |
| Pending sau lỗi/KILL | Không sửa JSON tay. Giữ nguyên, xác minh source/hash/lock/quota; xin duyệt resume để Bridge thực hiện journal recovery theo code đã test. |
| ENOSPC / lỗi ghi | Dừng, kiểm tra dung lượng và quyền, bảo toàn spool. Có thể có tail chưa commit; cần recovery được duyệt. |
| ID nguồn thay đổi | Dừng review; không tự thêm allow-source-replacement. Log của container cũ có thể chưa đọc hết. |
| Checkpoint hỏng | Giữ bằng chứng, không reset bootstrap. Khôi phục từ backup đã xác minh trong bước riêng; chưa nghiệm thu restore thật. |
| Alloy gửi nhưng Loki count thiếu | So cùng event-time/service/stream; kiểm tra retry/drop/discard, clock và chunk. Không kết luận mất dữ liệu từ một counter/query. |
| Backfill cũ >3h còn trong ingester | query_ingesters_within mặc định 3h; chờ idle flush 30 phút từ lần ghi cuối (check mỗi 30s), không tính từ timestamp log. Không ép flush/restart. |

Chưa có quy trình cleanup acknowledged end-to-end hoặc backup/restore production
được nghiệm thu. Xóa spool cũng có thể làm positions Alloy không còn khớp.
Docker json-file rotation có thể xóa log chưa đọc. Redaction best-effort, không
đảm bảo loại bỏ mọi bí mật; dashboard và log không công khai ra Internet.

## Thu thập thường xuyên — đề xuất, chưa bật

Trước mắt tiếp tục các đợt supervisor hữu hạn theo lịch vận hành được duyệt,
mỗi đợt một lần preflight/hậu kiểm; tránh yêu cầu hàng chục batch thủ công.
Chọn chu kỳ ngắn hơn thời gian log nguồn có thể bị rotation theo lưu lượng đo
thực tế, không dựa riêng số ngày. Không gọi supervisor hiện tại là daemon.

Nếu cần tự động hóa sau này: trước tiên thiết kế và test supervisor không giữ
root lâu dài, process-group/cgroup cleanup, lịch không chồng lấn, bảo vệ source
root-owned, quota/alerts và giao nhận-cleanup. Docker group dù tạm thời vẫn có
quyền điều khiển daemon rất rộng, không giới hạn chỉ BeautyGlow. Xem xét endpoint
proxy đọc được kiểm soát hoặc thiết kế collector thay thế trước khi cấp thường
trực. Đây là thay đổi thiết kế cần duyệt, không tự tạo systemd/cron/timer.

## Nghiệm thu và bảo mật

Chạy tests offline bằng user thường, không nhóm socket cho tiến trình test:
`python3 -B -m unittest discover -s tests/logging -p 'test_*.py' -q`.
Script `scripts/verify-logging-dashboard.py` chỉ truy vấn, dùng credential Grafana
cục bộ trong RAM; không in secret/raw log. Nó kiểm snapshot cố định 3.977 dòng,
không dùng nguyên assertion đó để đánh giá đợt live mới hoặc dữ liệu đã hết retention.
Dashboard: http://localhost:13000/d/beautyglow-logs-security (đăng nhập hiện có).
Prometheus: dashboard beautyglow-overview, 18 panel / 21 query.

Loki auth tắt, không publish host port; chỉ mạng Docker tin cậy. Exporter có
quyền quan sát host; quyền Docker của Bridge vẫn là rủi ro. ACL workspace trên
Windows còn rộng; không chạy code có quyền socket từ /mnt/d. Hardening và ACL
.env tạm dừng, không thuộc thay đổi vận hành này. Không đưa .env, secret, JSONL,
checkpoint, positions, WAL, volumes, dumps hoặc ảnh chưa rà soát lên Git.
