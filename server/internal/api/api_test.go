package api

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/cybersader/cleanyfin/server/internal/store"
)

func newTestServer(t *testing.T) http.Handler {
	t.Helper()
	st, err := store.Open(filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })
	return New(st, slog.New(slog.NewTextHandler(io.Discard, nil)))
}

func do(t *testing.T, h http.Handler, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	req.Header.Set("content-type", "application/json")
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	return rr
}

func countSegments(t *testing.T, body []byte) int {
	t.Helper()
	var resp struct {
		Segments []store.Segment `json:"segments"`
	}
	if err := json.Unmarshal(body, &resp); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	return len(resp.Segments)
}

// TestSubmitGetVoteHide exercises the core slice-1 flow: submit a segment, see
// it, reject bad input, then downvote it to the auto-hide threshold (R08).
func TestSubmitGetVoteHide(t *testing.T) {
	h := newTestServer(t)

	if rr := do(t, h, "GET", "/healthz", ""); rr.Code != http.StatusOK {
		t.Fatalf("healthz = %d", rr.Code)
	}

	rr := do(t, h, "POST", "/api/v1/segments",
		`{"fingerprint":"fp1","durationMs":1000,"startMs":10,"endMs":20,"category":"profanity","severity":2,"action":"mute","submitterId":"alice"}`)
	if rr.Code != http.StatusCreated {
		t.Fatalf("submit = %d body=%s", rr.Code, rr.Body.String())
	}
	var seg store.Segment
	if err := json.Unmarshal(rr.Body.Bytes(), &seg); err != nil {
		t.Fatalf("unmarshal segment: %v", err)
	}
	if seg.ID == "" {
		t.Fatal("submitted segment has no id")
	}

	if rr := do(t, h, "GET", "/api/v1/segments?fp=fp1", ""); countSegments(t, rr.Body.Bytes()) != 1 {
		t.Fatalf("expected 1 visible segment, got %d", countSegments(t, rr.Body.Bytes()))
	}

	if rr := do(t, h, "POST", "/api/v1/segments",
		`{"fingerprint":"x","startMs":1,"endMs":2,"category":"bogus","severity":1,"action":"mute","submitterId":"a"}`); rr.Code != http.StatusBadRequest {
		t.Fatalf("invalid category = %d, want 400", rr.Code)
	}

	do(t, h, "POST", "/api/v1/segments/"+seg.ID+"/vote", `{"submitterId":"bob","value":-1}`)
	do(t, h, "POST", "/api/v1/segments/"+seg.ID+"/vote", `{"submitterId":"carol","value":-1}`)
	if rr := do(t, h, "GET", "/api/v1/segments?fp=fp1", ""); countSegments(t, rr.Body.Bytes()) != 0 {
		t.Fatalf("after two downvotes expected 0 visible (auto-hidden at -2), got %d", countSegments(t, rr.Body.Bytes()))
	}

	if rr := do(t, h, "POST", "/api/v1/segments/does-not-exist/vote", `{"submitterId":"x","value":1}`); rr.Code != http.StatusNotFound {
		t.Fatalf("vote on missing segment = %d, want 404", rr.Code)
	}
}

// TestCorsPreflight verifies the marking PWA can preflight cross-origin calls.
func TestCorsPreflight(t *testing.T) {
	h := newTestServer(t)
	req := httptest.NewRequest(http.MethodOptions, "/api/v1/segments", nil)
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != http.StatusNoContent {
		t.Fatalf("preflight = %d, want 204", rr.Code)
	}
	if rr.Header().Get("Access-Control-Allow-Origin") == "" {
		t.Fatal("preflight missing Access-Control-Allow-Origin header")
	}
}
