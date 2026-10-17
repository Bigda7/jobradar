package main

import (
	"bufio"
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
)

func require(ok bool, message string) {
	if !ok {
		panic(message)
	}
}

func main() {
	root, err := os.MkdirTemp("", "caddy-isolated-probe-")
	if err != nil {
		panic(err)
	}
	defer os.RemoveAll(root)
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		panic(err)
	}
	cert := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "localhost"}, DNSNames: []string{"localhost"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, IsCA: true, BasicConstraintsValid: true}
	der, err := x509.CreateCertificate(rand.Reader, cert, cert, &key.PublicKey, key)
	if err != nil {
		panic(err)
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyDER, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		panic(err)
	}
	certPath, keyPath := filepath.Join(root, "fixture.crt"), filepath.Join(root, "fixture.key")
	if err = os.WriteFile(certPath, certPEM, 0600); err != nil {
		panic(err)
	}
	if err = os.WriteFile(keyPath, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER}), 0600); err != nil {
		panic(err)
	}
	var calls atomic.Int64
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		switch r.URL.Path {
		case "/echo":
			w.Header().Set("Content-Type", "application/json")
			body, _ := io.ReadAll(r.Body)
			json.NewEncoder(w).Encode(map[string]string{"authorization": r.Header.Get("Authorization"), "host": r.Host, "method": r.Method, "body": string(body), "query": r.URL.RawQuery, "forwarded_proto": r.Header.Get("X-Forwarded-Proto")})
		case "/large":
			w.Write(bytes.Repeat([]byte("x"), 1024*1024))
		case "/error":
			http.Error(w, "Synthetic upstream error", 500)
		case "/cookies":
			w.Header().Add("Set-Cookie", "first=one; HttpOnly")
			w.Header().Add("Set-Cookie", "second=two; Secure")
			w.Write([]byte("cookies"))
		case "/stream":
			w.Header().Set("Content-Type", "text/event-stream")
			flusher := w.(http.Flusher)
			fmt.Fprint(w, "data: first\n\n")
			flusher.Flush()
			time.Sleep(65 * time.Second)
			fmt.Fprint(w, "data: last\n\n")
			flusher.Flush()
		default:
			w.Write([]byte("healthy"))
		}
	}))
	defer upstream.Close()
	configPath := filepath.Join(root, "Caddyfile")
	config := fmt.Sprintf("{\n admin 127.0.0.1:2019\n http_port 8080\n https_port 8443\n default_bind 127.0.0.1\n}\nhttps://localhost:8443 {\n tls %s %s\n request_body {\n max_size 1KB\n }\n reverse_proxy %s {\n flush_interval -1\n }\n}\n", certPath, keyPath, upstream.URL)
	if err = os.WriteFile(configPath, []byte(config), 0600); err != nil {
		panic(err)
	}
	binary := os.Args[1]
	for _, command := range [][]string{{"validate", "--config", configPath, "--adapter", "caddyfile"}} {
		output, err := exec.Command(binary, command...).CombinedOutput()
		require(err == nil, "Synthetic config validation failed: "+string(output))
	}
	cmd := exec.Command(binary, "run", "--config", configPath, "--adapter", "caddyfile")
	var logs bytes.Buffer
	cmd.Stdout, cmd.Stderr = &logs, &logs
	require(cmd.Start() == nil, "Candidate process failed to start")
	defer func() { cmd.Process.Signal(syscall.SIGTERM); cmd.Wait() }()
	pool := x509.NewCertPool()
	require(pool.AppendCertsFromPEM(certPEM), "Fixture certificate could not be trusted")
	tlsConfig := &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}
	h2Transport := &http.Transport{TLSClientConfig: tlsConfig.Clone(), ForceAttemptHTTP2: true}
	defer h2Transport.CloseIdleConnections()
	h2 := &http.Client{Transport: h2Transport, Timeout: 80 * time.Second}
	h1TLS := tlsConfig.Clone()
	h1TLS.NextProtos = []string{"http/1.1"}
	h1Transport := &http.Transport{TLSClientConfig: h1TLS, TLSNextProto: map[string]func(string, *tls.Conn) http.RoundTripper{}}
	defer h1Transport.CloseIdleConnections()
	h1 := &http.Client{Transport: h1Transport, Timeout: 80 * time.Second}
	ready := false
	for attempt := 0; attempt < 100; attempt++ {
		response, err := h2.Get("https://localhost:8443/health")
		if err == nil {
			io.Copy(io.Discard, response.Body)
			response.Body.Close()
			ready = response.StatusCode == 200
			if ready {
				break
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	require(ready, "Candidate did not become ready")
	results := []string{"config_validation", "startup"}
	for _, version := range []uint16{tls.VersionTLS12, tls.VersionTLS13} {
		transport := &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: version, MaxVersion: version}}
		client := &http.Client{Transport: transport, Timeout: 5 * time.Second}
		response, err := client.Get("https://localhost:8443/health")
		require(err == nil, "Supported TLS handshake failed")
		response.Body.Close()
		require(response.TLS.Version == version, "Unexpected TLS version negotiated")
		transport.CloseIdleConnections()
	}
	results = append(results, "tls12_and_tls13")
	legacyTransport := &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS10, MaxVersion: tls.VersionTLS11}}
	legacyClient := &http.Client{Transport: legacyTransport, Timeout: 5 * time.Second}
	legacyResponse, legacyError := legacyClient.Get("https://localhost:8443/health")
	if legacyResponse != nil {
		legacyResponse.Body.Close()
	}
	require(legacyError != nil, "Legacy TLS unexpectedly accepted")
	legacyTransport.CloseIdleConnections()
	results = append(results, "legacy_tls_rejected")
	for _, item := range []struct {
		name     string
		client   *http.Client
		protocol int
	}{{"http1", h1, 1}, {"http2", h2, 2}} {
		request, _ := http.NewRequest("POST", "https://localhost:8443/echo?fixture=one", strings.NewReader("synthetic-body"))
		request.Header.Set("Authorization", "Bearer synthetic-fixture-not-a-secret")
		response, err := item.client.Do(request)
		require(err == nil, fmt.Sprintf("%s request failed: %v", item.name, err))
		var echo map[string]string
		err = json.NewDecoder(response.Body).Decode(&echo)
		response.Body.Close()
		require(err == nil && response.StatusCode == 200 && response.ProtoMajor == item.protocol, item.name+" protocol/status failed")
		require(echo["authorization"] == request.Header.Get("Authorization") && echo["body"] == "synthetic-body" && echo["method"] == "POST" && echo["host"] == "localhost:8443" && echo["query"] == "fixture=one" && echo["forwarded_proto"] == "https", item.name+" proxy contract changed")
		results = append(results, item.name+"_proxy_authorization_host_body_query")
	}
	redirectClient := &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, err := redirectClient.Get("http://localhost:8080/echo?redirect=one")
	require(err == nil, "HTTP redirect failed")
	response.Body.Close()
	require(response.StatusCode == 308 && response.Header.Get("Location") == "https://localhost/echo?redirect=one", fmt.Sprintf("HTTPS redirect changed: status=%d location=%s", response.StatusCode, response.Header.Get("Location")))
	results = append(results, "https_redirect")
	request, _ := http.NewRequest("GET", "https://localhost:8443/echo", nil)
	request.Host = "untrusted.example.invalid"
	before := calls.Load()
	response, err = h2.Do(request)
	require(err == nil, "Untrusted host probe failed")
	unmatchedBody, err := io.ReadAll(response.Body)
	response.Body.Close()
	require(err == nil && calls.Load() == before, fmt.Sprintf("Unmatched Host forwarded upstream: status=%d calls=%d", response.StatusCode, calls.Load()-before))
	require(response.StatusCode == 404 || response.StatusCode == 421 || (response.StatusCode == 200 && len(unmatchedBody) == 0), "Unexpected unmatched-host response")
	results = append(results, "untrusted_host_not_forwarded")
	response, err = h2.Post("https://localhost:8443/echo", "application/octet-stream", bytes.NewReader(bytes.Repeat([]byte("x"), 2048)))
	require(err == nil, "Request size probe failed")
	response.Body.Close()
	require(response.StatusCode == 413, fmt.Sprintf("Configured request body limit not enforced: status=%d", response.StatusCode))
	results = append(results, "configured_request_size_limit")
	for _, item := range []struct {
		path   string
		status int
		size   int
	}{{"/large", 200, 1024 * 1024}, {"/error", 500, -1}, {"/cookies", 200, -1}} {
		response, err = h2.Get("https://localhost:8443" + item.path)
		require(err == nil, "Upstream response probe failed")
		data, err := io.ReadAll(response.Body)
		response.Body.Close()
		require(err == nil && response.StatusCode == item.status, "Upstream status changed")
		if item.size >= 0 {
			require(len(data) == item.size, "Large response truncated")
		}
		if item.path == "/cookies" {
			require(len(response.Header.Values("Set-Cookie")) == 2, "Separate Set-Cookie fields merged")
		}
		results = append(results, "upstream"+strings.ReplaceAll(item.path, "/", "_"))
	}
	streamErrors := make(chan string, 2)
	for _, item := range []struct {
		name     string
		client   *http.Client
		protocol int
	}{{"http1", h1, 1}, {"http2", h2, 2}} {
		go func(name string, client *http.Client, protocol int) {
			start := time.Now()
			response, err := client.Post("https://localhost:8443/stream", "text/plain", strings.NewReader("synthetic"))
			if err != nil {
				streamErrors <- name + ": request failed"
				return
			}
			defer response.Body.Close()
			if response.ProtoMajor != protocol {
				streamErrors <- name + ": wrong protocol"
				return
			}
			reader := bufio.NewReader(response.Body)
			first, err := reader.ReadString('\n')
			if err != nil || first != "data: first\n" || time.Since(start) > 5*time.Second {
				streamErrors <- name + ": initial event buffered"
				return
			}
			rest, err := io.ReadAll(reader)
			if err != nil || !strings.Contains(string(rest), "data: last") || time.Since(start) < 65*time.Second {
				streamErrors <- name + ": idle stream truncated"
				return
			}
			streamErrors <- ""
		}(item.name, item.client, item.protocol)
	}
	for count := 0; count < 2; count++ {
		require(<-streamErrors == "", "Stream longer than 60 seconds failed")
	}
	results = append(results, "http1_post_stream_65s", "http2_post_stream_65s")
	output, err := exec.Command(binary, "reload", "--config", configPath, "--adapter", "caddyfile").CombinedOutput()
	require(err == nil, "Config reload failed: "+string(output))
	response, err = h2.Get("https://localhost:8443/health")
	require(err == nil, "Health after reload failed")
	response.Body.Close()
	require(response.StatusCode == 200, "Reload broke proxy")
	results = append(results, "reload_preserves_proxy")
	cmd.Process.Signal(syscall.SIGTERM)
	require(cmd.Wait() == nil, "Graceful shutdown failed")
	results = append(results, "graceful_shutdown")
	cmd = exec.Command(binary, "run", "--config", configPath, "--adapter", "caddyfile")
	cmd.Stdout, cmd.Stderr = &logs, &logs
	require(cmd.Start() == nil, "Restart failed")
	h1Transport.CloseIdleConnections()
	h2Transport.CloseIdleConnections()
	restarted := false
	for attempt := 0; attempt < 100; attempt++ {
		response, err = h2.Get("https://localhost:8443/health")
		if err == nil {
			io.Copy(io.Discard, response.Body)
			response.Body.Close()
			if response.StatusCode == 200 {
				restarted = true
				break
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	require(restarted, "Restart did not become ready within bounded probes")
	results = append(results, "restart_preserves_proxy")
	json.NewEncoder(os.Stdout).Encode(map[string]any{"passed": len(results), "checks": results, "external_network_required": false})
}
