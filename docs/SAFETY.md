# Phạm vi và bảo toàn dữ liệu

Chỉ được tạo/sửa file trong `D:\BeautyGlow-Website` theo phạm vi người dùng duyệt.
Lab `/home/dieu/lab-giam-sat` chỉ đọc; không sửa, xóa, di chuyển, commit, push
hoặc chạy script cài đặt. Không truy cập credential Lab để tái sử dụng.

Không thay đổi container, network, volume, database, Docker daemon hay WSL hiện có.
Không dùng `bai7-lab`, các network MSSV hoặc bất kỳ volume Lab cho BeautyGlow.
Không dùng quyền nâng cấp Lab 8 trước đây làm quyền thay đổi Lab hiện tại.

Mọi lệnh có thể thay đổi Docker/WSL hoặc tài nguyên ngoài thư mục dự án phải
được giải thích tác động và chờ người dùng phê duyệt trước. Bước hiện tại không
cho phép up/down, tạo tài nguyên, cài phần mềm, commit hoặc push.

Những quy tắc này là giới hạn thao tác, không phải khóa quyền kỹ thuật.
Project name/network riêng không cô lập hoàn toàn quyền của các container
giám sát có mount host hoặc Docker socket; phải đánh giá khi bổ sung exporters/log.

Bằng chứng Lab PASS ngày 01/10/2026 không xác nhận hiện trạng. Backup Ubuntu
trước di chuyển không thay thế backup Docker/Lab mới. Chưa ghi nhận backup mới,
kiểm tra port, khôi phục hoặc nghiệm thu giai đoạn 1 trong bước chuẩn bị này.
