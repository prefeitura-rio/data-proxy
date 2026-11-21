use std/assert
use ../lib.nu [checked gcp-credentials-problem]

# Verify that checked returns the output, passes the pipeline input, and names the command in a failure.
export def "test checked-returns-the-output-and-names-a-failure" []: nothing -> nothing {
    assert equal (checked 'echo' echo hi | str trim) hi
    assert equal ('abc' | checked 'cat' cat) abc

    let result = try { checked 'the false command' 'false'; 'no error' } catch {|err| $err.msg }

    assert str contains $result 'the false command failed'
}

# Verify that Google's answer invalid_grant marks the login as expired.
export def "test gcp-credentials-problem-reports-an-expired-login" []: nothing -> nothing {
    let credentials = {type: authorized_user client_id: id client_secret: secret refresh_token: token}
    let problem = gcp-credentials-problem $credentials {|form| {error: invalid_grant} }

    assert str contains $problem 'gcloud auth application-default login'
}

# Verify that a working login, a service account file and a network error give no problem.
export def "test gcp-credentials-problem-is-null-when-the-login-works-or-cannot-be-tested" []: nothing -> nothing {
    let user = {type: authorized_user client_id: id client_secret: secret refresh_token: token}

    assert equal (gcp-credentials-problem $user {|form| {access_token: abc} }) null
    assert equal (gcp-credentials-problem {type: service_account} {|form| {error: invalid_grant} }) null
    assert equal (gcp-credentials-problem $user {|form| {} }) null
}
