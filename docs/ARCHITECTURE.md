# Kiến trúc nền tảng dự kiến

```text
Browser localhost:<BEAUTYGLOW_HTTP_PORT>
  -> Nginx:80 [frontend]
  -> WordPress:80 [frontend + database]
  -> MySQL:3306 [database internal]

Browser localhost:<PHPMYADMIN_HTTP_PORT>
  -> phpMyAdmin:80 [database internal]
  -> MySQL:3306
```

Database: `beautyglow_catalog`. App user: `beautyglow_app`.
Volume MySQL: `beautyglow-website_mysql_data` -> `/var/lib/mysql`.
Volume WordPress: `beautyglow-website_wordpress_data` -> `/var/www/html`.
Nginx mount cấu hình từ repo ở chế độ chỉ đọc. Dữ liệu không tham chiếu Lab.

Frontend được phép egress để WordPress có thể tải bản dịch/plugin sau khi được
duyệt. Database internal hạn chế kết nối ngoài. phpMyAdmin vẫn publish localhost
để quản trị; network isolation không thay thế xác thực và kiểm thử truy cập.

Nhánh sau: exporters -> Prometheus -> Grafana; Promtail -> Loki -> Grafana;
Prometheus -> Alertmanager -> bao-dong. Chưa chọn cổng/tài nguyên cho các nhánh này.
Không mặc định dùng lại số container/targets hoặc datasource UID của Lab.
