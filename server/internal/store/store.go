// Package store is the SQLite persistence layer for cleanyfin segments.
//
// SQLite in WAL mode is the zero-ops default (backup = copy the file). A single
// open connection serializes writes (SQLite is single-writer); this is ample at
// v1 scale — graduate to Postgres only at SponsorBlock scale (tech-stack).
package store

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

// Store wraps the database handle.
type Store struct{ db *sql.DB }

// Segment is one crowdsourced tagged span. Times are integer milliseconds,
// release-relative (decision R04); the rich taxonomy lives here, not in
// Jellyfin's coarse enum (R14 correction).
type Segment struct {
	ID          string `json:"id"`
	Fingerprint string `json:"fingerprint"` // release fingerprint (moviehash + duration key, R04)
	DurationMs  int64  `json:"durationMs"`
	StartMs     int64  `json:"startMs"`
	EndMs       int64  `json:"endMs"`
	Category    string `json:"category"` // fixed taxonomy (R05)
	Severity    int    `json:"severity"` // 0-3 (R05)
	Action      string `json:"action"`   // mute|skip|mark (R05/R06)
	SubmitterID string `json:"submitterId"`
	Votes       int    `json:"votes"`
	Status      string `json:"status"`
	CreatedAt   int64  `json:"createdAt"`
}

const schema = `
CREATE TABLE IF NOT EXISTS segment (
  id           TEXT    PRIMARY KEY,
  fingerprint  TEXT    NOT NULL,
  duration_ms  INTEGER NOT NULL,
  start_ms     INTEGER NOT NULL,
  end_ms       INTEGER NOT NULL,
  category     TEXT    NOT NULL,
  severity     INTEGER NOT NULL,
  action       TEXT    NOT NULL,
  submitter_id TEXT    NOT NULL,
  votes        INTEGER NOT NULL DEFAULT 0,
  status       TEXT    NOT NULL DEFAULT 'pending',
  created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segment_fingerprint ON segment(fingerprint);

CREATE TABLE IF NOT EXISTS vote (
  segment_id   TEXT    NOT NULL,
  submitter_id TEXT    NOT NULL,
  value        INTEGER NOT NULL,
  created_at   INTEGER NOT NULL,
  PRIMARY KEY (segment_id, submitter_id)
);
`

// Open opens (creating if needed) the SQLite database in WAL mode and applies
// the schema. modernc.org/sqlite is pure Go, so no CGo/libc is required.
func Open(path string) (*Store, error) {
	dsn := fmt.Sprintf("file:%s?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(on)", path)
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1) // serialize writes; SQLite is single-writer
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{db: db}, nil
}

func (s *Store) Close() error                      { return s.db.Close() }
func (s *Store) Ping(ctx context.Context) error    { return s.db.PingContext(ctx) }

func newID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// InsertSegment stores a new pending segment and returns it with its assigned id.
func (s *Store) InsertSegment(ctx context.Context, seg Segment) (Segment, error) {
	seg.ID = newID()
	seg.Votes = 0
	seg.Status = "pending"
	seg.CreatedAt = time.Now().Unix()
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO segment (id,fingerprint,duration_ms,start_ms,end_ms,category,severity,action,submitter_id,votes,status,created_at)
		 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
		seg.ID, seg.Fingerprint, seg.DurationMs, seg.StartMs, seg.EndMs,
		seg.Category, seg.Severity, seg.Action, seg.SubmitterID, seg.Votes, seg.Status, seg.CreatedAt)
	if err != nil {
		return Segment{}, err
	}
	return seg, nil
}

// SegmentsByFingerprint returns visible segments for an exact release
// fingerprint. Auto-hide at vote score <= -2 (decision R08) is enforced here.
func (s *Store) SegmentsByFingerprint(ctx context.Context, fp string) ([]Segment, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT id,fingerprint,duration_ms,start_ms,end_ms,category,severity,action,submitter_id,votes,status,created_at
		 FROM segment WHERE fingerprint = ? AND votes > -2 AND status != 'hidden' ORDER BY start_ms`, fp)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make([]Segment, 0)
	for rows.Next() {
		var g Segment
		if err := rows.Scan(&g.ID, &g.Fingerprint, &g.DurationMs, &g.StartMs, &g.EndMs,
			&g.Category, &g.Severity, &g.Action, &g.SubmitterID, &g.Votes, &g.Status, &g.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, g)
	}
	return out, rows.Err()
}

// Vote records one vote per (segment, submitter) — a later vote replaces the
// earlier one — then recomputes and stores the segment's vote sum. Returns the
// new sum. Returns sql.ErrNoRows if the segment does not exist.
func (s *Store) Vote(ctx context.Context, segmentID, submitterID string, value int) (int, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()

	var one int
	if err := tx.QueryRowContext(ctx, `SELECT 1 FROM segment WHERE id = ?`, segmentID).Scan(&one); err != nil {
		return 0, err // sql.ErrNoRows when missing
	}
	if _, err := tx.ExecContext(ctx,
		`INSERT INTO vote (segment_id,submitter_id,value,created_at) VALUES (?,?,?,?)
		 ON CONFLICT(segment_id,submitter_id) DO UPDATE SET value=excluded.value, created_at=excluded.created_at`,
		segmentID, submitterID, value, time.Now().Unix()); err != nil {
		return 0, err
	}
	var sum int
	if err := tx.QueryRowContext(ctx, `SELECT COALESCE(SUM(value),0) FROM vote WHERE segment_id = ?`, segmentID).Scan(&sum); err != nil {
		return 0, err
	}
	if _, err := tx.ExecContext(ctx, `UPDATE segment SET votes = ? WHERE id = ?`, sum, segmentID); err != nil {
		return 0, err
	}
	if err := tx.Commit(); err != nil {
		return 0, err
	}
	return sum, nil
}

// Stats returns basic counts, handy for health dashboards and smoke tests.
func (s *Store) Stats(ctx context.Context) (map[string]int, error) {
	var total, hidden int
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM segment`).Scan(&total); err != nil {
		return nil, err
	}
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM segment WHERE votes <= -2 OR status = 'hidden'`).Scan(&hidden); err != nil {
		return nil, err
	}
	return map[string]int{"segments": total, "hidden": hidden, "visible": total - hidden}, nil
}
