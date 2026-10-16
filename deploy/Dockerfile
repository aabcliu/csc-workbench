FROM --platform=$BUILDPLATFORM maven:3.9-eclipse-temurin-17 AS bridge-builder

WORKDIR /build/java_bridge
COPY engine/java_bridge/pom.xml ./pom.xml
COPY engine/java_bridge/src ./src
RUN mvn -q package

FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        graphviz \
        openjdk-17-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/csc
COPY deploy/requirements.lock /opt/csc/deploy/requirements.lock
RUN python -m pip install --no-cache-dir -r /opt/csc/deploy/requirements.lock
COPY engine /opt/csc/engine
COPY web /opt/csc/web
COPY deploy/smoke_check.py /opt/csc/deploy/smoke_check.py
COPY examples /opt/csc/examples
COPY benchmarks /opt/csc/benchmarks
COPY LICENSE THIRD_PARTY_NOTICES.md /opt/csc/
COPY licenses /opt/csc/licenses
COPY --from=bridge-builder \
    /build/java_bridge/target/csc-bridge-0.1.0-jar-with-dependencies.jar \
    /opt/csc/engine/java_bridge/target/csc-bridge-0.1.0-jar-with-dependencies.jar

RUN python -m pip install --no-cache-dir --no-deps \
    -e /opt/csc/engine \
    -e /opt/csc/web

ENV CSC_ENGINE_ROOT=/opt/csc/engine
ENV CSC_FRONTEND_ROOT=/opt/csc/web/frontend
ENV CSC_WEB_DATA_ROOT=/data

WORKDIR /opt/csc/web
EXPOSE 8765

HEALTHCHECK --interval=5s --timeout=5s --start-period=15s --retries=6 \
    CMD python -c "import json, urllib.request; h=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)); assert h['status']=='ok' and h['bridge_built'] and all(h['tools'].values())"

CMD ["csc-web", "--host", "0.0.0.0", "--port", "8765"]
