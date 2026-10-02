use std/log

# Log an error and raise a labeled error in one call.
export def fail [message: string, context: record<command: string, span: record>]: nothing -> error {
    log error $message
    error make {
        msg: $message
        label: {text: $context.command, span: $context.span}
    }
}

# Poll a readiness check until it returns true or attempts are exhausted.
export def poll [check: closure, config: record<interval: duration, max_attempts: int>]: nothing -> bool {
    mut attempts = $config.max_attempts

    while $attempts > 0 {
        if (do $check) { return true }
        sleep $config.interval
        $attempts -= 1
    }

    false
}
