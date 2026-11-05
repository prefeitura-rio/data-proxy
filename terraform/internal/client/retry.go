package client

import (
	"context"
	"errors"
	"time"
)

// Backoff sets how RetryNoRow waits between two reads. Each wait doubles until it reaches Max, and Max zero
// means no limit. Timeout limits all the reads together.
type Backoff struct {
	Initial time.Duration
	Max     time.Duration
	Timeout time.Duration
}

// DefaultBackoff waits 100 ms, then 200 ms, 400 ms, 800 ms and 1 s, for up to 5 s in total.
var DefaultBackoff = Backoff{Initial: 100 * time.Millisecond, Max: time.Second, Timeout: 5 * time.Second}

// RetryNoRow calls read again while it wraps ErrNoRow, because a standby behind PostgREST can miss a row that the
// primary wrote a moment ago. It returns the first other result, the last ErrNoRow after the timeout, or the error
// of the context. It doesn't retry ErrNotFound, which doesn't change with time.
func RetryNoRow(ctx context.Context, backoff Backoff, read func(context.Context) error) error {
	return retryNoRow(ctx, backoff, time.Now, sleep, read)
}

// retryNoRow is RetryNoRow with a clock and a wait that a test replaces.
func retryNoRow(
	ctx context.Context,
	backoff Backoff,
	now func() time.Time,
	wait func(context.Context, time.Duration) error,
	read func(context.Context) error,
) error {
	deadline := now().Add(backoff.Timeout)
	delay := backoff.Initial

	for {
		err := read(ctx)
		if !errors.Is(err, ErrNoRow) {
			return err
		}

		if delay <= 0 || now().Add(delay).After(deadline) {
			return err
		}

		if waitErr := wait(ctx, delay); waitErr != nil {
			return waitErr
		}

		delay *= 2

		if backoff.Max > 0 {
			delay = min(delay, backoff.Max)
		}
	}
}

// sleep waits for the duration, or returns the error of the context when it ends first.
func sleep(ctx context.Context, duration time.Duration) error {
	timer := time.NewTimer(duration)
	defer timer.Stop()

	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
