# Kiểm tra cấu hình không khởi động dịch vụ

Docker Compose CLI có thể parse file mà không kết nối Docker Engine.
`config` không pull image, tạo container/network/volume hay khởi động WSL.

Kiểm tra chưa nội suy biến, không cần `.env`:

```sh
docker compose --env-file .env.example config --no-interpolate --quiet
```

Kiểm tra mô hình sau nội suy bằng giá trị giả chỉ trong môi trường tiến trình
kiểm tra. Hai cổng dưới đây chỉ phục vụ parse, không chứng minh cổng trống:

```sh
MYSQL_PASSWORD=validation_only_not_a_secret \
MYSQL_ROOT_PASSWORD=validation_only_different_not_a_secret \
BEAUTYGLOW_HTTP_PORT=18080 PHPMYADMIN_HTTP_PORT=18081 \
docker compose --env-file .env.example config --quiet
```

Không dùng các giá trị giả này để triển khai. Khi dùng `.env` thật, chỉ dùng
`config --quiet`, không lưu hoặc chia sẻ output đã nội suy có credential.

Compose chỉ kiểm tra mô hình YAML/Compose, không xác nhận Nginx syntax, image
entrypoint, DNS, kết nối MySQL, quyền ghi volume, HTTP headers hoặc port rảnh.
`nginx -t` bằng image cần tạo container nên không chạy ở bước này. Nếu không
có Nginx native phù hợp, ghi rõ kiểm tra runtime này còn chờ phê duyệt Docker.

Kiểm thử ở bước sau khi được duyệt: HTTP qua proxy; DB authentication; phpMyAdmin;
headers; dữ liệu còn sau restart; tên/labels tài nguyên riêng; không publish DB;
backup/restore trong tài nguyên thử riêng. Không thực hiện ở bước chuẩn bị file.
