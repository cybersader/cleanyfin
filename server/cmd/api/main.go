// Command cleanyfin-api is the crowdsourced content-segment API server.
//
// It is the source-of-truth hub (decision R02): the Jellyfin plugin and the
// marking PWA are thin clients of this API. It ships ONLY timestamps + category
// metadata (R01) — never audio/video. SQLite-backed, single static binary,
// configured entirely by environment variables (tech-stack decision).
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/cybersader/cleanyfin/server/internal/api"
	"github.com/cybersader/cleanyfin/server/internal/store"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	addr := getenv("CLEANYFIN_ADDR", ":8080")
	dbPath := getenv("CLEANYFIN_DB", "./cleanyfin.db")

	st, err := store.Open(dbPath)
	if err != nil {
		logger.Error("failed to open store", "err", err, "db", dbPath)
		os.Exit(1)
	}
	defer st.Close()

	srv := &http.Server{
		Addr:              addr,
		Handler:           api.New(st, logger),
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		logger.Info("cleanyfin-api listening", "addr", addr, "db", dbPath)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("server error", "err", err)
			os.Exit(1)
		}
	}()

	// Graceful shutdown on SIGINT/SIGTERM (resilience: clean SQLite close).
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	logger.Info("shutting down")

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		logger.Error("graceful shutdown failed", "err", err)
	}
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
