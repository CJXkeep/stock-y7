# 趋势分析实时买卖点工具 - 运行时镜像
# 仅标准库 + 内置 libs/（requests 全家）第三方依赖，无需 pip install
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8

WORKDIR /app

# 拷贝运行所需源码（.dockerignore 负责排除 docs/tests/.git/运行数据等）
COPY . .

EXPOSE 8795

# 运行数据（journal/pool/results/digest）通过卷持久化，避免重建镜像丢失：
#   docker run -d -p 8795:8795 -v "$(pwd)/data:/app/data" qushi:latest
# 注意：data/ 是 Python 包（含 kline_fetcher.py），用绑定挂载整个 data/ 目录
# 而非独立数据卷，否则会遮蔽镜像内的代码模块。
CMD ["python", "app.py"]