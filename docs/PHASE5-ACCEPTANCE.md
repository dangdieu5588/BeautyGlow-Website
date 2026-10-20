# BeautyGlow — nghiệm thu cuối Giai đoạn 5

## Nghiệm thu bổ sung Alerting — 21/10/2026 (UTC+7)

Giai đoạn 5 **PASS trong phạm vi bài thực hành: logging tập trung theo đợt,
dashboard và cảnh báo Gmail có thông báo phục hồi thực nhận**. Logging đã được
push trong commit `9906158`; không triển khai hoặc kiểm thử lại phần đã PASS.
Hồ sơ Logging và kế hoạch Git cũ bên dưới là lịch sử, không phải trạng thái Git
hay hướng dẫn triển khai hiện tại.

| Hạng mục Alerting | Kết quả / bằng chứng |
|---|---|
| Alertmanager | PASS: service riêng, UID/GID 65534:65534, chỉ mạng monitoring, không publish host port, gossip tắt. |
| Prometheus | PASS: kết nối `alertmanager:9093`; nạp đúng 4 rules, health=ok; 6/6 targets UP. |
| Gmail SMTP | PASS: `smtp.gmail.com:587`, STARTTLS bắt buộc; receiver gửi cả firing/resolved; gom nhóm 30s/5m, repeat 4h. |
| Credential | PASS: file ngoài repository `/var/lib/beautyglow-secrets/alertmanager-gmail.yml`, root:65534 0440; parent root:root 0700; mount read-only. App Password nhập ẩn trong terminal, không đưa vào Git/output. |
| Pending → Firing | PASS: rule thử riêng `BeautyGlowEmailDeliveryTest`, for=1m; quan sát cả hai trạng thái trên API Prometheus. |
| FIRING thực nhận | PASS: người vận hành xác nhận email Gmail lúc **03:43 ngày 21/10/2026 UTC+7**. |
| RESOLVED thực nhận | PASS: người vận hành xác nhận email Gmail cùng alert lúc **03:48 ngày 21/10/2026 UTC+7**. |
| Bằng chứng gửi | Sau hai thông báo: attempts=2, completed=2, failures=0; kết luận thực nhận dựa thêm xác nhận người dùng, không chỉ counter. |
| Dọn thử nghiệm | PASS: bỏ đường dẫn rule thử, reload Prometheus bằng SIGHUP, xóa file thử; giữ nguyên bốn rule chính thức. Không restart dịch vụ khi thử/dọn. |
| Không ảnh hưởng dịch vụ | PASS: website/MySQL/Grafana/Loki/Alloy hoạt động; 14 container, chỉ Prometheus được recreate để thêm mount ở bước triển khai đầu, chỉ Alertmanager được recreate khi đổi mount Gmail. |
| Offline Alerting | PASS: promtool config/rules, amtool config; 8 tình huống / 15 assertions. |

Test ID: `BG-GMAIL-20261020T204136Z` (UTC). Không lưu email nguyên văn, ảnh,
địa chỉ cá nhân hoặc credential vào hồ sơ Git. Có xác nhận người dùng về hai email;
không truy cập hộp thư hoặc tạo ảnh minh chứng tự động.

Phạm vi commit Alerting riêng (7 file):

- `compose.yaml`
- `config/prometheus/prometheus.yml`
- `config/prometheus/rules/beautyglow-alerts.yml`
- `config/alertmanager/alertmanager.yml` (fallback không gửi, không phải config Gmail thật)
- `config/alertmanager/README.md`
- `scripts/verify-alerting.py`
- `docs/PHASE5-ACCEPTANCE.md`

Loại trừ `.env`, credential/file runtime bên ngoài repo, rule thử, checkpoint,
spool, volume, `config/prometheus/prometheus.batch43.yml` và ảnh monitoring chưa
rà soát. Commit/push chỉ danh sách trên lên nhánh hiện tại sau kiểm tra staged
content, remote và upstream; không amend, force-push hoặc reset.

Vận hành, bảo vệ credential và rollback xem
[`config/alertmanager/README.md`](../config/alertmanager/README.md).
Chưa kết luận production 24/7: Bridge chưa chạy nền; cleanup spool/backup restore
thật, giám sát đĩa, cảnh báo metric bị thiếu và Hardening vẫn là công việc tiếp
theo. Hệ thống chạy trong WSL chỉ gửi cảnh báo khi host và các service hoạt động;
không có hệ thống giám sát bên ngoài khi cả máy tắt. Gmail App Password có thể
bị thu hồi; cần kiểm tra lại sau khi đổi credential, mạng hoặc receiver.

## Hồ sơ nghiệm thu Logging trước đây (lịch sử)

Ngày kiểm tra: 20/10/2026 (UTC+7). Nhánh hiện tại: `main`, HEAD `e165a50`.
Không commit/push, không chạy live/restart trong lượt nghiệm thu này.

## Kết quả

| Hạng mục | Đánh giá / bằng chứng |
|---|---|
| Pipeline đã triển khai | PASS: 13 container running; healthcheck hiện có healthy; Loki/Alloy/Grafana sẵn sàng. |
| Supervisor catch-up | PASS lần live được duyệt trước: 234 poll, 348 giây, exit 0 CAUGHT_UP, thêm 3.765 dòng; không cần KILL. |
| JSONL → Loki → Grafana | PASS: 3.977/3.977 theo cùng khoảng thời gian và sáu service/stream; không dựa riêng HTTP 204. |
| Dashboard logging | PASS: 14 panel, 15 LogQL; frame số/Time/Value, nhãn, HTTP500 và hai lát pie được kiểm chứng; người dùng đã xác nhận trực quan thành công. |
| Prometheus regression | PASS: file/dashboard biểu thức không đổi; 18 panel, 21 query có dữ liệu, 6/6 targets up. |
| Offline logging tests | PASS: 239/239 Linux, gồm 32 supervisor tests. |
| Compose / syntax | PASS: config --quiet; Python/JSON và diff whitespace kiểm tra riêng. |
| Private state | PASS: bootstrap 10:13:25Z, scan cursors 18:16:40Z ngày 19/10, không pending/checkpoint.tmp; 932.005 byte spool. |
| Live holder cleanup | PASS: không Bridge/supervisor hoặc UID474 giữ nhóm socket khi tổng kết. |
| Protected /opt | PASS: Bridge 474:474 0500; supervisor root:474 0550; policy root:474 0440; hash khớp bản được duyệt. |
| Thu thập nền / lịch tự động | CHƯA TRIỂN KHAI, có chủ đích; chưa cấp quyền socket thường trực. |
| Cleanup spool / backup restore thật | CHƯA NGHIỆM THU; không tự xóa hoặc giả định retention giải quyết mọi dung lượng. |
| Hardening Giai đoạn 6 | Ngoài phạm vi; ACL .env/workspace và các rủi ro host/network chưa xử lý. |

Kết luận: **đủ điều kiện nghiệm thu Giai đoạn 5 trong phạm vi bài thực hành,
pipeline thu thập theo đợt có giám sát và dashboard đã kiểm chứng**. Không kết
luận vận hành production 24/7, khôi phục sau thảm họa hoặc exactly-once toàn tuyến.
Hồ sơ test cố định không chứng minh nguồn Docker không từng mất log do rotation.

## Phạm vi commit chính xác

Chỉ các file sau thuộc đề xuất commit Giai đoạn 5 (23 file, gồm tài liệu cập nhật):

- `compose.yaml`
- `README.md`
- `config/alloy/README.md`
- `config/alloy/bridge-policy.json`
- `config/alloy/config.alloy`
- `config/loki/README.md`
- `config/loki/loki.yml`
- `config/grafana/provisioning/datasources/loki.yml`
- `config/grafana/dashboards/beautyglow-logs-security.json`
- `scripts/beautyglow-log-bridge.py`
- `scripts/beautyglow-log-supervisor.py`
- `scripts/verify-logging-dashboard.py`
- `docs/LOGGING-5.3.6C-OFFLINE.md`
- `docs/LOGGING-SUPERVISOR.md`
- `docs/LOGGING-DASHBOARD.md`
- `docs/LOGGING-OPERATIONS.md`
- `docs/PHASE5-ACCEPTANCE.md`
- `tests/logging/test_bridge.py`
- `tests/logging/test_docker_adapter.py`
- `tests/logging/test_live_bridge.py`
- `tests/logging/test_spool_stop_limit.py`
- `tests/logging/test_supervisor.py`
- `tests/logging/test_timestamp_contract.py`

Đã sửa nội dung README hiện hành và đánh dấu rõ phần chuẩn bị 5.2 là lịch sử,
tránh người vận hành nhầm rằng logging chưa triển khai. Không thay đổi các config
Prometheus, Nginx, Bridge/Supervisor/Policy trong lượt tổng kết tài liệu này.

## Loại trừ khỏi commit

- `config/prometheus/prometheus.batch43.yml`: bản sao byte-identical với config
  Prometheus hiện tại, không thuộc logging; không xóa trong lượt này.
- `docs/evidence/monitoring/prometheus-targets.png`: ảnh Giai đoạn monitoring,
  chưa rà soát pixel về thông tin nhạy cảm, không thuộc commit logging hiện tại.
- `.env`, secrets/, data/, backups/, tmp/, credentials, TLS private keys, dumps,
  database files, JSONL/checkpoint/lock thật, positions/WAL/volume, file /opt,
  file ACL backup, raw logs và ảnh chưa rà soát. Không coi .gitignore là chứng nhận an toàn.
- Mọi thư mục tạm `tests/logging/_supervisor-*` hoặc fixture tạm do test đang chạy;
  kiểm tra tests đã dọn sạch trước staging. Chỉ stage sáu test_*.py nêu trên.

Quét mẫu key/token và literal-secret chỉ có candidate trong test redaction
`test_password_assignments_redacted` (dữ liệu giả), không phải mật khẩu vận hành.
Các đường nhạy cảm đang tracked chỉ có .gitkeep; .env không tracked. Quét mẫu
không phải đảm bảo tuyệt đối, và chưa rà soát pixel của ảnh bị loại trừ.

## Kế hoạch commit/push — chưa thực hiện

1. Giữ nguyên nhánh main theo yêu cầu. Sau phê duyệt, xác minh lại branch/HEAD,
   working tree, danh sách 23 file và hash bản chạy /opt so với source.
2. Stage **đúng danh sách file trên** bằng đường dẫn cụ thể; không dùng git add .,
   git add -A hoặc add cả thư mục tests/config/docs. Không stage ảnh hoặc bản batch43.
3. Kiểm `git diff --cached --name-status`, `git diff --cached --check`, đọc diff
   cả file mới (diff hiện tại không hiển thị nội dung untracked), quét secrets
   trên staged blobs và đối chiếu danh sách. Không in cấu hình chứa secrets.
4. Tạo một commit mô tả pipeline, protected supervisor, dashboard và runbook:
   `feat: complete supervised BeautyGlow logging and Grafana dashboard`.
   Không amend, rewrite history, merge hoặc dùng force.
5. Trước push, kiểm remote chính xác; fetch/status upstream theo phê duyệt.
   Nếu diverged hoặc remote thay đổi, dừng báo cáo. Chỉ push thường main lên
   origin khi được phê duyệt rõ; không reset để giải quyết xung đột.
6. Đối chiếu commit/file list và trạng thái sau push; không đưa state runtime vào Git.

Nên phê duyệt riêng commit và push để được xem commit cụ thể trước xuất bản.
Các thao tác Git ở trên là kế hoạch, chưa stage/commit/fetch/push.

## Công việc vận hành tiếp theo

Xem LOGGING-OPERATIONS.md: tiếp tục supervisor theo đợt được duyệt, chưa bật nền.
Nếu cần tự động hóa, phải giải quyết lịch không chồng lấn, quota/alerts, đặc quyền
socket và cleanup có bằng chứng giao nhận trước. Backup/restore volume/state
và thử nghiệm outage end-to-end thật cần bước riêng được duyệt. Khôi phục fake
journal đã test không thay thế restore dữ liệu vận hành thật.
