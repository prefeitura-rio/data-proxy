package client_test

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"terraform-provider-data-proxy/internal/client"
	"testing"
	"time"
)

// fakeClock is a clock that moves only when the code waits, so a test needs no real delay.
type fakeClock struct {
	current time.Time
	waits   []time.Duration
}

// now returns the time of the fake clock.
func (fc *fakeClock) now() time.Time {
	return fc.current
}

// wait records the duration, moves the fake clock forward and returns at once.
func (fc *fakeClock) wait(_ context.Context, duration time.Duration) error {
	fc.waits = append(fc.waits, duration)
	fc.current = fc.current.Add(duration)

	return nil
}

// errOther is an error that the retry does not handle.
var errOther = errors.New("other")

// retryOnMissingRow returns the error of a read that finds no row, which the retry handles.
func retryOnMissingRow() error {
	return fmt.Errorf("no row of projects matches the filters: %w", client.ErrNoRow)
}

func TestRetryNoRow(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name      string
		backoff   client.Backoff
		results   []error
		wantErr   error
		wantReads int
		wantWaits []time.Duration
	}{
		{
			name:      "first read succeeds",
			backoff:   client.DefaultBackoff(),
			results:   []error{nil},
			wantReads: 1,
		},
		{
			name:      "other error isn't retried",
			backoff:   client.DefaultBackoff(),
			results:   []error{errOther},
			wantErr:   errOther,
			wantReads: 1,
		},
		{
			name:      "HTTP 404 isn't retried",
			backoff:   client.DefaultBackoff(),
			results:   []error{client.ErrorOfKind(404, "PGRST205", client.ErrNotFound)},
			wantErr:   client.ErrNotFound,
			wantReads: 1,
		},
		{
			name:      "row appears on the third read",
			backoff:   client.DefaultBackoff(),
			results:   []error{retryOnMissingRow(), retryOnMissingRow(), nil},
			wantReads: 3,
			wantWaits: []time.Duration{100 * time.Millisecond, 200 * time.Millisecond},
		},
		{
			name:      "other error after a retry is returned",
			backoff:   client.DefaultBackoff(),
			results:   []error{retryOnMissingRow(), errOther},
			wantErr:   errOther,
			wantReads: 2,
			wantWaits: []time.Duration{100 * time.Millisecond},
		},
		{
			name:      "gives up at the timeout of the default backoff",
			backoff:   client.DefaultBackoff(),
			wantErr:   client.ErrNoRow,
			wantReads: 8,
			wantWaits: []time.Duration{
				100 * time.Millisecond, 200 * time.Millisecond, 400 * time.Millisecond, 800 * time.Millisecond,
				time.Second, time.Second, time.Second,
			},
		},
		{
			name:      "zero max doubles without a limit",
			backoff:   client.Backoff{Initial: time.Second, Timeout: 10 * time.Second},
			wantErr:   client.ErrNoRow,
			wantReads: 4,
			wantWaits: []time.Duration{time.Second, 2 * time.Second, 4 * time.Second},
		},
		{
			name:      "zero initial reads once",
			backoff:   client.Backoff{Timeout: time.Second},
			wantErr:   client.ErrNoRow,
			wantReads: 1,
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			clock := &fakeClock{current: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)}
			reads := 0

			err := client.RetryNoRowWithClock(
				context.Background(),
				testCase.backoff,
				clock.now,
				clock.wait,
				func(context.Context) error {
					reads++

					if reads <= len(testCase.results) {
						return testCase.results[reads-1]
					}

					return retryOnMissingRow()
				},
			)

			if testCase.wantErr == nil && err != nil {
				t.Fatalf("error = %v, want nil", err)
			}

			if !errors.Is(err, testCase.wantErr) {
				t.Fatalf("error = %v, want %v", err, testCase.wantErr)
			}

			if reads != testCase.wantReads {
				t.Errorf("reads = %d, want %d", reads, testCase.wantReads)
			}

			if !slices.Equal(clock.waits, testCase.wantWaits) {
				t.Errorf("waits = %v, want %v", clock.waits, testCase.wantWaits)
			}
		})
	}
}

func TestRetryNoRowStopsWithTheContext(t *testing.T) {
	t.Parallel()

	ctx, cancel := context.WithCancel(context.Background())
	reads := 0

	err := client.RetryNoRow(ctx, client.Backoff{Initial: time.Hour, Timeout: 2 * time.Hour}, func(context.Context) error {
		reads++

		cancel()

		return retryOnMissingRow()
	})

	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want %v", err, context.Canceled)
	}

	if reads != 1 {
		t.Errorf("reads = %d, want 1", reads)
	}
}
