# Bước 5.3.6C — kết quả kiểm thử offline

Ngày kiểm tra: 19/10/2026. Chỉ kiểm thử với dữ liệu giả và spool tạm trong
repository; không chạy bridge live/catch-up, WSL hoặc Docker. Không truy cập
checkpoint/bootstrap/spool thật, không đổi Compose/container và không commit/push.

## Thay đổi

- `scripts/beautyglow-log-bridge.py`: tùy chọn `--stop-spool-bytes 8388608`;
  mặc định `None`. Giá trị phải là số nguyên dương, không vượt hard limit
  16.777.216 byte của policy.
- `tests/logging/test_spool_stop_limit.py`: 26 tests cho cấu hình, sát biên,
  lock, journal/checkpoint tạm, JSONL, rotation, recovery/resume, ENOSPC và CLI.
- Lượt tiếp tục này bổ sung mã thoát riêng `3` cho `SPOOL_STOP_LIMIT` và một
  test CLI dùng mock; lỗi khác vẫn trả `2`.

## Cơ chế giới hạn

Quota cộng `st_size` của mọi regular file trong spool, gồm byte lock,
JSONL và checkpoint. Trước append, dự trù đồng thời toàn bộ bản journal
prepared, checkpoint committed và bản ghi mới. Mỗi lần lưu checkpoint còn
kiểm tra riêng dung lượng bản sao tạm trước khi tạo file. Việc dự trù bảo thủ
có thể dừng khi tổng dung lượng hiện tại vẫn thấp hơn ngưỡng.

Thao tác có tổng dự kiến bằng ngưỡng được phép; lớn hơn ngưỡng bị từ chối.
Khi từ chối vì ngưỡng, không ghi bản ghi đó, không tiến cursor cho thao tác
đó, không tự retry hoặc xóa JSONL để lấy chỗ. Những bản ghi đã commit trước
đó vẫn được giữ. Recovery của một lần ghi bị ngắt có thể xử lý phần đuôi chưa
commit theo cơ chế journal hiện có; ENOSPC vẫn có thể gây ghi dở và cần recovery.
Đây không phải cơ chế ngăn filesystem hết dung lượng.

## Kiểm chứng

`python -B -m unittest discover -s tests/logging -p 'test_*.py' -q`:
**207 PASS, 0 FAIL**. `git diff --check`: thành công. Vì hai file code/test
chưa được Git theo dõi, đã kiểm tra bổ sung cú pháp và trailing whitespace
trực tiếp trên cả hai file.

## Giới hạn nghiệm thu

Chỉ xác minh giới hạn byte nội dung file trong điều kiện một writer tuân thủ
lock và không có tiến trình ngoài sửa spool. Chưa bảo đảm 8 MiB dung lượng
vật lý gồm allocation blocks, directory/inode, filesystem journal hoặc
metadata do filesystem tự tạo. Chưa kiểm chứng fsync/quyền trên Linux thật
trong lượt này. Do đó **chưa kết luận PASS cho yêu cầu trần vật lý tuyệt đối**
và kết quả offline không cho phép tự chạy catch-up thật.

Đã dừng sau kiểm thử offline theo phạm vi bước 5.3.6C.
