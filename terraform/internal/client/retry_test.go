package client

import (
	"context"
	"errors"
	"slices"
	"testing"
	"time"
)

// fakeClock moves its time forward by each wait instead of sleeping.
type fakeClock struct {
	current time.Time
	waits   []time.Duration
}

func (f *fakeClock) now() time.Time {
	return f.current
}

func (f *fakeClock) wait(_ context.Context, duration time.Duration) error {
	f.waits = append(f.waits, duration)
	f.current = f.current.Add(duration)

	return nil
}

var errOther = errors.New("other")

func notFound() error {
	return &Error{Status: 404, Message: "no row", kind: ErrNotFound}
}

func TestRetryNotFound(t *testing.T) {
	tests := []struct {
		id        string
		backoff   Backoff
		results   []error
		wantErr   error
		wantReads int
		wantWaits []time.Duration
	}{
		{
			id:        "first read succeeds",
			backoff:   DefaultBackoff,
			results:   []error{nil},
			wantReads: 1,
		},
		{
			id:        "other error isn't retried",
			backoff:   DefaultBackoff,
			results:   []error{errOther},
			wantErr:   errOther,
			wantReads: 1,
		},
		{
			id:        "row appears on the third read",
			backoff:   DefaultBackoff,
			results:   []error{notFound(), notFound(), nil},
			wantReads: 3,
			wantWaits: []time.Duration{100 * time.Millisecond, 200 * time.Millisecond},
		},
		{
			id:        "other error after a retry is returned",
			backoff:   DefaultBackoff,
			results:   []error{notFound(), errOther},
			wantErr:   errOther,
			wantReads: 2,
			wantWaits: []time.Duration{100 * time.Millisecond},
		},
		{
			id:        "gives up at the timeout of the default backoff",
			backoff:   DefaultBackoff,
			wantErr:   ErrNotFound,
			wantReads: 8,
			wantWaits: []time.Duration{
				100 * time.Millisecond, 200 * time.Millisecond, 400 * time.Millisecond, 800 * time.Millisecond,
				time.Second, time.Second, time.Second,
			},
		},
		{
			id:        "zero max doubles without a limit",
			backoff:   Backoff{Initial: time.Second, Timeout: 10 * time.Second},
			wantErr:   ErrNotFound,
			wantReads: 4,
			wantWaits: []time.Duration{time.Second, 2 * time.Second, 4 * time.Second},
		},
		{
			id:        "zero initial reads once",
			backoff:   Backoff{Timeout: time.Second},
			wantErr:   ErrNotFound,
			wantReads: 1,
		},
	}

	for _, tt := range tests {
		t.Run(tt.id, func(t *testing.T) {
			clock := &fakeClock{current: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)}
			reads := 0

			err := retryNotFound(context.Background(), tt.backoff, clock.now, clock.wait, func(context.Context) error {
				reads++

				if reads <= len(tt.results) {
					return tt.results[reads-1]
				}

				return notFound()
			})

			if tt.wantErr == nil && err != nil {
				t.Fatalf("error = %v, want nil", err)
			}

			if !errors.Is(err, tt.wantErr) {
				t.Fatalf("error = %v, want %v", err, tt.wantErr)
			}

			if reads != tt.wantReads {
				t.Errorf("reads = %d, want %d", reads, tt.wantReads)
			}

			if !slices.Equal(clock.waits, tt.wantWaits) {
				t.Errorf("waits = %v, want %v", clock.waits, tt.wantWaits)
			}
		})
	}
}

func TestRetryNotFoundStopsWithTheContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	reads := 0

	err := RetryNotFound(ctx, Backoff{Initial: time.Hour, Timeout: 2 * time.Hour}, func(context.Context) error {
		reads++

		cancel()

		return notFound()
	})

	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want %v", err, context.Canceled)
	}

	if reads != 1 {
		t.Errorf("reads = %d, want 1", reads)
	}
}
