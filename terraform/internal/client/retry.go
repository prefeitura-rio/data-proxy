package client

import (
	"context"
	"errors"
	"time"
)

// Backoff sets how RetryNotFound waits between two reads.
type Backoff struct {
	// Initial is the wait after the first read.
	Initial time.Duration
	// Max limits one wait. Each wait doubles until it reaches Max. Zero means no limit.
	Max time.Duration
	// Timeout limits the time of all the reads. RetryNotFound doesn't start a wait that ends after it.
	Timeout time.Duration
}

// DefaultBackoff waits 100 ms, then 200 ms, 400 ms, 800 ms and 1 s, for up to 5 s in total.
var DefaultBackoff = Backoff{Initial: 100 * time.Millisecond, Max: time.Second, Timeout: 5 * time.Second}

// RetryNotFound calls read again while it returns an error that wraps ErrNotFound. The provider reads from a
// replica (PgDog) behind PostgREST, and the replica can miss a row that the primary wrote a moment ago.
// RetryNotFound returns the first result that isn't ErrNotFound, the last ErrNotFound after the timeout, or the
// error of the context.
func RetryNotFound(ctx context.Context, backoff Backoff, read func(context.Context) error) error {
	return retryNotFound(ctx, backoff, time.Now, sleep, read)
}

func retryNotFound(
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
		if !errors.Is(err, ErrNotFound) {
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
