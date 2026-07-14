ARG BASE_IMAGE=insta360-stitch-base:latest
FROM ${BASE_IMAGE}

WORKDIR /opt/insta360-stitch

COPY src/insta360_media_stitcher.cc /tmp/src/insta360_media_stitcher.cc

RUN g++ -std=c++17 -O2 -Wall -Wextra \
      -I/opt/insta360/include \
      /tmp/src/insta360_media_stitcher.cc \
      -L/opt/insta360/lib \
      -Wl,-rpath,/opt/insta360/lib \
      -lMediaSDK \
      -pthread \
      -o /usr/local/bin/insta360_media_stitcher && \
    rm -rf /tmp/src

COPY scripts/stitch_batch.py /usr/local/bin/insta360-stitch-batch
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/insta360-stitch-batch /usr/local/bin/entrypoint.sh

VOLUME ["/data"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["--help"]
