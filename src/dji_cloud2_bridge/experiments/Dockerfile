# Research-only build environment. The image digest pins the base; Debian/Ubuntu
# transitive package versions are recorded at build time, not asserted immutable.
FROM docker.osgeo.org/geoserver@sha256:1e6dfdaf67398b790eaa68fa212492d168aab603f9d2214629978d43ee6f6e45
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential=12.10ubuntu1 cmake=3.28.3-1build7 swig=4.2.0-2ubuntu1 \
    zlib1g=1:1.3.dfsg-3.1ubuntu2.3 zlib1g-dev=1:1.3.dfsg-3.1ubuntu2.3 patch=2.7.6-7build3 \
    && dpkg-query -W > /opt/cloud2-build-package-manifest.txt \
    && rm -rf /var/lib/apt/lists/*
