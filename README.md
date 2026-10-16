# BeautyGlow – Website quảng bá mỹ phẩm

BeautyGlow là dự án cá nhân thuộc **Đề 03 – Website Quảng bá Sản phẩm**, môn
Triển khai và Quản trị Hệ thống Phần mềm. Website giới thiệu mỹ phẩm đa thương
hiệu, phục vụ mục đích học tập; không đại diện chính thức cho các thương hiệu.

Tên hiển thị: **BeautyGlow – Mỹ phẩm & Chăm sóc sắc đẹp**.

## Trạng thái hiện tại

- Đã triển khai bốn dịch vụ WordPress, MySQL, phpMyAdmin và Nginx bằng Docker Compose.
- WordPress đã cài đặt bằng tiếng Việt, đang dùng Twenty Twenty-Five.
- Theme `beautyglow` và plugin `beautyglow-products` chưa triển khai; hai thư mục
  hiện chỉ có `.gitkeep`, chưa được bind mount vào container WordPress.
- Đã kiểm chứng MySQL và WordPress healthy; Nginx không restart loop, kiểm tra
  `nginx -t` thành công. Website và trang đăng nhập phpMyAdmin trả HTTP 200
  từ cả Windows và Ubuntu sau khi sửa mạng phpMyAdmin.
- Chưa kiểm thử đầy đủ backup/restore hoặc tính bền vững sau restart.
- Monitoring, logging và cảnh báo mới có thư mục dự phòng; chưa triển khai cho BeautyGlow.

Kết quả trên ghi nhận trong quá trình triển khai, không thay thế kiểm tra trạng
thái hiện tại bằng các lệnh bên dưới.

## Truy cập cục bộ

| Thành phần | Địa chỉ |
| --- | --- |
| Website | http://localhost:18080 |
| WordPress Admin | http://localhost:18080/wp-admin/ |
| phpMyAdmin | http://localhost:18081 |

Nginx và phpMyAdmin chỉ publish trên địa chỉ loopback của host. Chưa cấu hình
HTTPS hoặc truy cập công khai. MySQL và WordPress không publish cổng trực tiếp.
Thông tin đăng nhập được quản lý riêng, không được ghi trong repository.

## Kiến trúc đang triển khai

Project Compose cố định: **`beautyglow-website`**.

```text
Browser -> localhost:18080 -> Nginx -> WordPress -> MySQL
Browser -> localhost:18081 -> phpMyAdmin -> MySQL
```

| Dịch vụ | Image khai báo | Vai trò |
| --- | --- | --- |
| wordpress | wordpress:php8.3-apache | CMS và quản trị nội dung |
| mysql | mysql:8.4 | Lưu database BeautyGlow |
| phpmyadmin | phpmyadmin:5.2-apache | Công cụ quản trị database |
| nginx | nginx:1.28-alpine | Reverse proxy và security headers |

Các image đã được sử dụng để chạy hệ thống; tag không phải digest bất biến.
Không tự pull/nâng cấp image khi chỉ muốn kiểm tra trạng thái hoặc khởi động
lại các container hiện có.

Hai network riêng:

- `beautyglow-website_frontend`: Nginx, WordPress và phpMyAdmin.
- `beautyglow-website_database`: mạng internal cho MySQL, WordPress và phpMyAdmin.

phpMyAdmin nối cả hai mạng để publish cổng localhost và truy cập database.
Nginx phân giải lại upstream WordPress qua DNS Docker khi địa chỉ container thay đổi.
MySQL có healthcheck truy vấn database; WordPress có healthcheck HTTP.
WordPress/phpMyAdmin đợi MySQL healthy, Nginx đợi WordPress healthy khi Compose khởi tạo.

Hai named volume riêng:

- `beautyglow-website_mysql_data` -> `/var/lib/mysql`.
- `beautyglow-website_wordpress_data` -> `/var/www/html`.

Volume thuộc Docker Engine native trong Ubuntu WSL2, không nằm trong thư mục
`data/` trên ổ D. Không sử dụng tài nguyên Lab 7–8.

## Cấu trúc repository

```text
BeautyGlow-Website/
├── compose.yaml
├── .env.example
├── .gitignore
├── .gitattributes
├── config/
│   ├── nginx/default.conf
│   ├── mysql/init/
│   ├── prometheus/rules/
│   ├── grafana/
│   │   ├── dashboards/
│   │   └── provisioning/
│   │       ├── datasources/
│   │       └── dashboards/
│   ├── exporters/
│   ├── loki/
│   ├── promtail/
│   └── alertmanager/
├── wordpress/
│   ├── themes/beautyglow/
│   └── plugins/beautyglow-products/
├── docs/
│   ├── ARCHITECTURE.md
│   └── SAFETY.md
├── tests/README.md
├── evidence/public/
├── secrets/
├── data/
└── backups/
```

Các thư mục dự phòng dùng `.gitkeep`; chúng không tự kích hoạt dịch vụ, theme,
plugin hoặc SQL. Dữ liệu trong `secrets/`, `data/`, `backups/` bị Git loại trừ.
`evidence/public/` chỉ dành cho minh chứng đã rà soát, che thông tin nhạy cảm.

`docs/ARCHITECTURE.md` và `docs/SAFETY.md` là tài liệu thiết kế ban đầu;
README này mô tả cập nhật mạng phpMyAdmin và trạng thái triển khai hiện tại.

## Điều kiện và môi trường

Các lệnh dưới đây chạy **trong terminal Ubuntu WSL2**, tại:

```sh
cd /mnt/d/BeautyGlow-Website
```

Cần Docker Engine đang hoạt động, Docker Compose plugin và file `.env` riêng.
`.env.example` là mẫu trống, không đủ để khởi động. Với một bản clone mới, chuẩn
bị `.env` cục bộ từ mẫu, tạo các mật khẩu ngẫu nhiên riêng và chọn cổng host
sau khi kiểm tra xung đột; không sao chép thông tin đăng nhập từ môi trường khác.
Không ghi giá trị `.env` vào README, terminal minh chứng hoặc Git.

Không ghi đè `.env` hiện có. Đổi mật khẩu trong `.env` không tự cập nhật mật khẩu
trong volume MySQL đã được khởi tạo.

## Khởi động

Kiểm tra cú pháp trước; chỉ khởi động khi kiểm tra thành công:

```sh
docker compose -p beautyglow-website --env-file .env config --quiet &&
docker compose -p beautyglow-website --env-file .env up -d
```

Lệnh `up -d` có thể tạo hoặc cập nhật tài nguyên BeautyGlow và tải image còn thiếu.
Chỉ chạy khi được phép triển khai. Không chạy trong thư mục Lab hoặc đổi project name.
Với hệ thống đã dừng bằng `stop` và không thay đổi cấu hình, có thể dùng `start`
để khởi động lại các container hiện có:

```sh
docker compose -p beautyglow-website --env-file .env start
```

Sau khi Engine/WSL khởi động lại vẫn cần kiểm tra health và HTTP; không mặc định
các dịch vụ hoặc thứ tự sẵn sàng luôn được khôi phục đúng.

## Kiểm tra và chụp minh chứng

```sh
docker compose -p beautyglow-website --env-file .env ps
curl -fsSL -o /dev/null -w 'Website HTTP %{http_code}\n' http://localhost:18080
curl -fsSL -o /dev/null -w 'phpMyAdmin HTTP %{http_code}\n' http://localhost:18081
docker compose -p beautyglow-website --env-file .env exec -T nginx nginx -t
```

Kết quả mong đợi: đủ bốn service Running; MySQL và WordPress healthy; hai URL
trả HTTP 200; Nginx kiểm tra cấu hình thành công. HTTP 200 của phpMyAdmin mới
xác nhận trang đăng nhập hoạt động, chưa chứng minh thao tác quản trị DB thành công.
Không dùng `config` thiếu `--quiet` để chụp minh chứng vì output có thể chứa secret.

## Dừng an toàn

Dừng riêng project BeautyGlow, giữ nguyên container và named volume:

```sh
docker compose -p beautyglow-website --env-file .env stop --timeout=-1
```

Timeout `-1` cho phép chờ graceful shutdown, không tự ép SIGKILL sau một khoảng
chờ cố định. Nếu quá trình dừng kéo dài, kiểm tra trạng thái và báo lỗi; không
ép kill hoặc reset database. Dừng dịch vụ có thể ghi dữ liệu shutdown bình thường.

Không dùng `down -v`, prune, xóa volume hoặc thao tác trực tiếp sửa/xóa thư mục
volume để dừng hệ thống. Không tác động container/volume/network Lab 7–8.

## Bảo mật và giới hạn

- Credential nằm trong `.env` bị Git ignore; `.env.example` không chứa credential.
- Database dùng tài khoản ứng dụng riêng, không publish cổng host.
- Nginx có nosniff, SAMEORIGIN, Referrer-Policy, Permissions-Policy; đã kiểm tra
  các header này qua HTTP. Chưa triển khai HTTPS/HSTS/CSP.
- WordPress tắt trình chỉnh sửa file PHP qua giao diện quản trị.
- Các service có log rotation và `no-new-privileges`; chưa nghiệm thu non-root
  hoặc toàn bộ hardening cuối kỳ.
- Không đưa dump, backup, token, output cấu hình chứa secret hoặc ảnh chụp lộ
  thông tin đăng nhập vào repository. `.gitignore` không thay thế rà soát nội dung.
- Mỗi lệnh thao tác Docker phải giới hạn đúng project BeautyGlow và phạm vi được duyệt.

## Phạm vi website giai đoạn tiếp theo

Đúng **10 sản phẩm đa thương hiệu** theo lựa chọn của người thực hiện, phân vào
Chăm sóc da, Trang điểm và Chống nắng. Một số phiên bản/màu/dung tích còn chờ xác
nhận trước khi nhập dữ liệu. Không tự bổ sung sản phẩm hoặc bịa giá/thông số.

Giao diện dự kiến theo chủ đề đầm sen hồng và lá sen xanh; tiếng Việt, responsive.
Chức năng gồm trang chủ, danh sách/danh mục/chi tiết sản phẩm, tìm kiếm, giới thiệu,
liên hệ và quản trị sản phẩm. Không có giỏ hàng, đơn hàng hoặc thanh toán.

Ảnh và thông tin sản phẩm cần có nguồn và được rà soát trước khi sử dụng.
Các thương hiệu được nhắc tới nhằm giới thiệu sản phẩm trong bài thực hành;
BeautyGlow không phải website chính thức hoặc tuyên bố là đại lý của các hãng.
