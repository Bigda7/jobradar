FROM golang:1.26.9-bookworm@sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c AS build

ENV GOTOOLCHAIN=local CGO_ENABLED=0 GOOS=linux
WORKDIR /source
COPY source.tar.gz /source.tar.gz
RUN echo 'e86c49311c0c9f7e9b3b80527f0139e5739dfe28effeaa41c444dd743f0bbd7e  /source.tar.gz' | sha256sum -c - \
    && tar -xzf /source.tar.gz --strip-components=1 -C /source
COPY build_candidate.sh verify_graph.go integration_probe.go /tools/
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    bash /tools/build_candidate.sh resolve

FROM scratch AS dependency-review
COPY --from=build /artifacts/metadata/ /

FROM build AS compile
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    bash /tools/build_candidate.sh compile

FROM scratch AS artifacts
COPY --from=compile /artifacts/ /
