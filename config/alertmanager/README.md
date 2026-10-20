# BeautyGlow Alertmanager — vận hành Gmail SMTP đã nghiệm thu

Ngày nghiệm thu: 21/10/2026 (UTC+7). Logging đã PASS được giữ nguyên.
Prometheus -> Alertmanager -> Gmail SMTP STARTTLS -> email FIRING / RESOLVED.
Người vận hành đã xác nhận cả hai email thật: FIRING 03:43, RESOLVED 03:48,
cùng `BeautyGlowEmailDeliveryTest`. Xem `docs/PHASE5-ACCEPTANCE.md`.

## Cấu hình đang chạy và bảo mật

- Alertmanager `prom/alertmanager:v0.34.1`, UID/GID 65534:65534, root filesystem
  read-only, cap_drop ALL, no-new-privileges; gossip tắt.
- Chỉ gắn `beautyglow-website_monitoring`, không publish host port. Mạng monitoring
  có kết nối egress để gửi SMTP; không thay đổi thành Docker `internal: true`.
- State riêng: `beautyglow-website_alertmanager_data`; giữ nguyên khi redeploy.
- Runtime config: `/var/lib/beautyglow-secrets/alertmanager-gmail.yml` trên Ubuntu,
  root:65534 0440, parent root:root 0700; bind read-only vào
  `/etc/alertmanager/alertmanager.yml`. Docker daemon giải quyết đường dẫn parent;
  UID65534 trong container đọc file mount mà không cần quyền đọc parent trên host.
- File `config/alertmanager/alertmanager.yml` trong repository là fallback receiver
  rỗng, **không gửi email**, không phải bản Gmail thật và hiện không được mount.
- SMTP `smtp.gmail.com:587`, `smtp_require_tls: true`, xác minh TLS mặc định;
  App Password nhập ẩn qua terminal. Địa chỉ gửi/nhận và App Password chỉ trong
  runtime file; không copy file đó vào Git, output, ảnh hay biến Compose.
- Gmail receiver có `send_resolved: true`. Group theo project/alertname/job,
  group_wait 30s, group_interval 5m, repeat_interval 4h. Đây là hạn chế gửi lặp,
  không phải cam kết exactly-once; Gmail có thể lọc Spam hoặc trì hoãn thư.

## Rules chính thức và ý nghĩa

Prometheus nạp `/etc/prometheus/rules/beautyglow-alerts.yml` từ bind read-only,
evaluate mỗi 15s. Không có rule thử trong trạng thái nghiệm thu.

| Rule | Điều kiện | for | Mức độ |
|---|---|---|---|
| BeautyGlowTargetDown | `up == 0` của project | 2m | warning |
| BeautyGlowMySQLDown | `mysql_up == 0`, exporter còn scrape được | 1m | critical |
| BeautyGlowNginxDown | `nginx_up == 0`, exporter còn scrape được | 1m | critical |
| BeautyGlowHTTPProbeFailed | `probe_success == 0`, probe còn scrape được | 2m | critical |

Mọi rule lọc project BeautyGlow; rule backend thêm job và gate `up == 1` để
phân biệt exporter không truy cập được với exporter báo backend lỗi. MySQL check
có thể lỗi vì quyền/kết nối; Nginx check có thể lỗi vì stub_status. Probe là tuyến
nội bộ Nginx -> WordPress, không đo cổng Windows/Internet. Metric thiếu hoặc target
bị xóa khỏi config không tự trở thành down. Không thêm filesystem alert khi chưa
bật collector. Chưa scrape riêng Alertmanager/Loki/Alloy hoặc giám sát máy từ ngoài.

## Kiểm tra thường ngày — chỉ đọc

Từ Ubuntu đang chạy, không tự khởi động hệ thống đang dừng:

```sh
docker compose config --quiet
docker compose ps
curl -fsS http://127.0.0.1:19090/-/ready
curl -fsS http://127.0.0.1:19090/api/v1/rules
curl -fsS http://127.0.0.1:19090/api/v1/targets
curl -fsS http://127.0.0.1:19090/api/v1/alertmanagers
docker compose exec -T prometheus wget -q -O /dev/null http://alertmanager:9093/-/ready
```

Kỳ vọng 14 container running, đúng 4 rules health=ok, 6/6 targets UP, 1 Alertmanager.
Inactive là bình thường khi không có lỗi. Kiểm tra email thật khi nghiệm thu;
Prometheus firing/HTTP 200 hoặc counter gửi không tự chứng minh thư đã vào inbox.
Không xuất raw logs, Env/inspect hoặc `/api/v2/status` nguyên văn vì chứa cấu hình.
Nếu cần xác thực runtime file, dùng amtool check-config dưới quyền cho phép,
chỉ báo exit code, không in output lỗi có thể chứa dữ liệu nhạy cảm.
Theo dõi counter notification failures, rule evaluation failures và config reload
success; counter reset khi recreate, vì vậy so sánh delta trong cùng lifecycle.

## Kiểm thử offline và thử email có kiểm soát

```sh
python3 -I -B scripts/verify-alerting.py --promtool /path/to/promtool --amtool /path/to/amtool
```

Script dùng python3-yaml, không Docker/API, không đọc runtime credential và chỉ
viết file tạm. Nó kiểm mount/isolation, fallback không gửi, promtool config/rules,
amtool fallback và 8 tình huống / 15 assertions (healthy, pending/for, firing,
phục hồi, exporter down, project khác, metric thiếu). Tool binary phải từ nguồn
chính thức, đúng phiên bản; đã kiểm với promtool 3.15.0 và amtool 0.34.1.

Thử email live cần phê duyệt riêng để tránh gửi ngoài ý muốn. Tạo rule thử riêng
với nhãn test_id duy nhất, project BeautyGlow, for=1m, điều kiện tổng hợp có hạn;
không dừng application. Thêm riêng đường dẫn rule thử và validate trước khi
SIGHUP Prometheus; không cần restart hoặc enable web lifecycle. Quan sát Pending
-> Firing -> người nhận xác nhận email. Sau đó đổi duy nhất expr thử thành false,
validate/reload; chờ group_interval có thể 5 phút và xác nhận email RESOLVED.
Cuối cùng bỏ đường dẫn và file thử, validate/reload, xác nhận còn đúng 4 rules.
Không commit rule thử hoặc state của lần thử; không tự gửi lại khi lỗi.

## Khởi động lại, đổi credential và rollback

Không dùng whole-project up/down hoặc xóa volume. Khi được duyệt triển khai từ
repo trên máy mới, phải provision runtime credential an toàn trước; Compose có
`create_host_path: false` để từ chối mount file thiếu. Repository không đủ để gửi
Gmail nếu thiếu file riêng. Không dùng fallback rồi kết luận gửi email PASS.

App Password có thể bị thu hồi khi tài khoản thay đổi. Đổi credential bằng nhập
ẩn trong terminal, không đặt trong command line/history/env. Giữ backup bảo vệ
ngoài repo và validate file mới trước khi reload hoặc recreate riêng Alertmanager.
Nếu thay file bằng atomic rename, bind hiện tại có thể giữ inode cũ: recreate
riêng Alertmanager để nhận file mới; không giả định SIGHUP luôn nhận nội dung mới.
Kiểm tra TLS, counters và thực nhận sau thay đổi. Không sửa quyền socket Docker.

Rollback Gmail (chỉ khi được duyệt): đổi riêng source bind của Alertmanager từ
runtime file sang `./config/alertmanager/alertmanager.yml`, validate Compose rồi
`docker compose up -d --no-deps --pull never alertmanager`. Cách này giữ volume,
Prometheus, 4 rules và ứng dụng, nhưng **tắt gửi email**. Không xóa credential hoặc
volume; khôi phục source mount bảo vệ để bật Gmail trở lại sau khi sửa lỗi.
Rollback toàn bộ Alerting là bước riêng: dùng config cũ đã xác minh cho Prometheus,
giữ TSDB và chỉ thay hai service liên quan; không tác động Logging/WordPress/MySQL.

SMTP lỗi: kiểm tra chỉ metadata/quyền, DNS/egress TCP587, TLS/time, App Password và
Spam qua công cụ không lộ credential; không hạ TLS hoặc thay đổi database. Nếu
hệ thống không thể gửi email, không tuyên bố nghiệm thu lại chỉ từ trạng thái UP.
Khi máy WSL tắt, hệ thống không gửi cảnh báo; giám sát ngoài máy chưa triển khai.

Tham khảo chính thức: [Alertmanager configuration](https://prometheus.io/docs/alerting/latest/configuration/),
[alert rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/),
[promtool tests](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/),
[Google App Passwords](https://support.google.com/accounts/answer/185833).
