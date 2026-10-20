# BeautyGlow — Giai đoạn 7: kiểm thử và nghiệm thu tổng thể

Ngày nghiệm thu: 21/10/2026 (UTC+7). **PASS trong phạm vi bài thực hành**, với
giới hạn công khai dưới đây. Chỉ kiểm tra GET/HEAD/API, metadata và config; không
sửa WordPress/MySQL, restart container, chạy lại Bridge, gửi lại email thử hoặc
backup/restore lại. Không tác động Lab hoặc xóa volume.

## Bảng nghiệm thu

| Hạng mục | Kết quả | Bằng chứng |
|---|---|---|
| Trang chủ | PASS | HTTP200, 4 thẻ sản phẩm, nội dung theme đúng. |
| Danh sách/danh mục | PASS | 10 sản phẩm; Chăm sóc da6, Trang điểm3, Chống nắng1. |
| Chi tiết BG001–BG010 | PASS | 10/10 HTTP200, mã đúng, ảnh local HEAD200, có product-detail. |
| Tìm kiếm | PASS | Dior trả1; từ khóa không tồn tại trả0 và thông báo rõ. |
| Giới thiệu/liên hệ | PASS | HTTP200; liên hệ dẫn GitHub Issues, không giả lập gửi form. |
| phpMyAdmin | PASS | Trang HTTP200; không đăng nhập hoặc ghi DB để thử. |
| Compose/containers | PASS | config --quiet; 14/14 running, healthcheck hiện có healthy. Service không khai báo healthcheck ghi not-configured, không gọi healthy. |
| Network/volume | PASS | 3 mạng riêng, database internal; 7 volume production hiện hữu; không mount nhầm môi trường restore. |
| Sau restart | PASS theo bằng chứng cũ có giới hạn | Restart policy unless-stopped của14 service, volume bền vững, hồ sơ WSL startup và recreate riêng Prometheus/Alertmanager/Blackbox trước đây; không diễn tập restart toàn stack lần mới. |
| Prometheus | PASS | 6/6 targets UP, 4 rules chính thức health=ok/inactive; 1 Alertmanager. |
| Grafana | PASS | Dashboard overview18 panel dữ liệu (6 row nhóm riêng), logging14 panel; query definitions khớp file và datasource health OK. Dùng lại nghiệm thu trực quan Giai đoạn5, không chụp lại tự động. |
| Loki/Alloy | PASS | Readiness200; dùng lại bằng chứng JSONL→Loki→Grafana3977/3977, không đếm lại. |
| Gmail Alertmanager | PASS | Config native hợp lệ, STARTTLS587/send_resolved còn bật; metadata credential đúng; dùng lại xác nhận FIRING03:43 và RESOLVED03:48 của người nhận, không gửi lại. |
| Hardening/secrets | PASS theo Giai đoạn6 | Non-root phù hợp, no-new-privileges, loopback/internal DB, headers, protected secrets và ACL .env đã duyệt; không thay ACL mới. |
| Backup/restore | PASS theo Giai đoạn6 | 12 bảng/412 dòng, 64 file, 14/14 ảnh gốc; checksum và restore cô lập khớp, SELECT thành công/CREATE bị từ chối1142. Container thử đã dừng, volume giữ lại. |
| Git | PASS phạm vi | Main sau Giai đoạn6:8dfe7d9; chỉ tài liệu/kết quả Giai đoạn7 được phép thêm, loại trừ hai file untracked cũ. |

Kết quả hiện tại: `PHASE7-RESULTS.json`. Bằng chứng được tái sử dụng:

- `PHASE5-ACCEPTANCE.md`: Logging239 tests, dashboard thực tế,3977logs và Gmail thật.
- `PHASE6-HARDENING-BACKUP.md`, `PHASE6-RESULTS.json`, `PHASE6-WINDOWS-ACL.json`:
  hardening,12test an toàn, backup/restore và đính chính charset Unicode.
- Commit Giai đoạn5:9906158(logging),6d136c2(alerting); Giai đoạn6:8dfe7d9.

Không coi tài liệu cũ là chứng minh dịch vụ luôn UP; readiness/targets/dashboard
availability đã kiểm nhanh ở lượt này. Không lặp lại test lưu trữ, outage, email
hay thao tác đặc quyền chỉ để tăng số lượng minh chứng.

## Giới hạn còn lại

- Local HTTP, chưa HTTPS/HSTS/public deployment; chỉ dùng môi trường học tập.
- Bridge thu thập theo đợt được duyệt, chưa daemon24/7; không diễn giải dữ liệu log
  lịch sử như dữ liệu realtime. Dashboard log mặc định dùng khoảng nghiệm thu.
- Gmail chỉ gửi khi WSL/services chạy; không có external monitoring khi cả máy tắt.
- Backup cùng host, chưa offsite/encrypted disaster recovery; không test restore
  đè production. Môi trường restore riêng đã stop, volume/network vẫn tồn tại.
- Không có bài cold restart toàn stack mới. Nếu rubric yêu cầu demo trực tiếp,
  cần phê duyệt riêng, bảo toàn dữ liệu và baseline trước/sau; không tự thực hiện.
- Một số master/entrypoint và cAdvisor giữ ngoại lệ root có lý do trong Phase6;
  config workspace ngoài .env vẫn có ACL rộng. Quyền Docker/root vẫn rất lớn.
- Liên hệ là liên kết GitHub Issues, cần tài khoản GitHub; không có gửi mail từ
  form, giỏ hàng hoặc thanh toán. Không coi đây là lỗi chức năng đã cam kết.
- Lỗi media32 trước là verifier thiếu utf8mb4, đã sửa; ảnh thật không bị thay đổi.

## Ảnh minh chứng cần chụp (chưa tự chụp trong lượt này)

Ưu tiên tái sử dụng ảnh đã duyệt. Ảnh mới chỉ chụp giao diện cần thiết, không có
credential, Env/inspect, raw logs, SQL/backup, người dùng hoặc thông tin cá nhân.
Ảnh chưa rà soát không tự stage vào Git.

1. Trang chủ và danh sách đủ10 sản phẩm; một ảnh cho ba danh mục nếu đủ rõ.
2. Chi tiết sản phẩm có ảnh; tìm kiếm Dior và trạng thái không có kết quả.
3. Giới thiệu/liên hệ, thể hiện rõ GitHub Issues thay vì form gửi mail.
4. Compose ps14 container, cột trạng thái; không chụp docker inspect/env.
5. Prometheus Targets6/6UP và Rules4 rule, không có rule thử.
6. Grafana overview18/logging14 panel, đúng time range của dữ liệu log.
7. Hai email FIRING/RESOLVED đã có: che địa chỉ và dữ liệu riêng, giữ tên alert,
   trạng thái và thời gian; không gửi lại để chụp.
8. Evidence Phase6 đã lọc: checksum backup, restore summary, SELECT44 và lỗi1142;
   không chụp nội dung dump/credential. Có thể dùng JSON kết quả thay screenshot.

`docs/evidence/monitoring/prometheus-targets.png` cũ vẫn chưa rà soát nội dung ảnh;
không được coi là ảnh đã duyệt và không đưa vào commit này.

## Demo ngắn (khoảng 5–7 phút)

1. Mở http://localhost:18080: trang chủ -> danh sách -> danh mục -> chi tiết ảnh.
2. Tìm Dior rồi từ khóa không tồn tại; mở giới thiệu/liên hệ, giải thích scope.
3. Hiển thị Compose ps và sơ đồ mạng/volume trong tài liệu, không restart.
4. Mở http://localhost:19090/targets (6UP), trang Rules (4 rule).
5. Mở http://localhost:13000: overview rồi Logs & Security; dùng khoảng log
   19/10/2026 10:13:34–18:16:40 UTC cho3977logs, không chọn Last5minutes rồi
   kết luận thiếu dữ liệu. Đăng nhập riêng, không hiện mật khẩu.
6. Trình bày ảnh email đã che thông tin và kết quả Phase6 backup/restore/SELECT-only.
7. Kết thúc bằng phạm vi PASS và giới hạn24/7/HTTPS/offsite/restart nói trên.

## Git và rollback

Chỉ `docs/PHASE7-ACCEPTANCE.md` và `docs/PHASE7-RESULTS.json` thuộc commit này.
Không stage `.env`, secrets, backup, volume/state, raw log, ảnh chưa duyệt,
`config/prometheus/prometheus.batch43.yml` hoặc thay đổi các phase đã PASS.
Kiểm staged diff/secret, branchmain và remote đúng BeautyGlow trước push thường;
không reset/amend/merge/force-push. Thay đổi Giai đoạn7 chỉ tài liệu, không có
runtime rollback; sửa tài liệu bằng commit tiếp theo nếu có bằng chứng mới.
