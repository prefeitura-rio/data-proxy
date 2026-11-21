package client

import (
	"errors"
	"net/http"
	"testing"
)

// TestKindOf checks that a SQLSTATE decides the kind before the HTTP status does, and that an unknown status has none.
func TestKindOf(t *testing.T) {
	tests := []struct {
		name   string
		status int
		code   string
		want   error
	}{
		{name: "unique violation", status: http.StatusConflict, code: "23505", want: ErrConflict},
		{name: "foreign key violation", status: http.StatusConflict, code: "23503", want: ErrConflict},
		{name: "check violation", status: http.StatusBadRequest, code: "23514", want: ErrInvalid},
		{name: "42501 with HTTP 401", status: http.StatusUnauthorized, code: "42501", want: ErrForbidden},
		{name: "42501 with HTTP 403", status: http.StatusForbidden, code: "42501", want: ErrForbidden},
		{name: "409 without a code", status: http.StatusConflict, want: ErrConflict},
		{name: "400 without a code", status: http.StatusBadRequest, want: ErrInvalid},
		{name: "403 without a code", status: http.StatusForbidden, want: ErrForbidden},
		{name: "401 without a code", status: http.StatusUnauthorized, want: ErrUnauthorized},
		{name: "404 without a code", status: http.StatusNotFound, want: ErrNotFound},
		{name: "an unknown status", status: http.StatusTeapot},
		{name: "a 500 with an unknown code", status: http.StatusInternalServerError, code: "XX000"},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			got := kindOf(testCase.status, testCase.code)

			if (testCase.want == nil && got != nil) || (testCase.want != nil && !errors.Is(got, testCase.want)) {
				t.Errorf("kindOf(%d, %q) = %v, want %v", testCase.status, testCase.code, got, testCase.want)
			}
		})
	}
}

// TestParseError checks the fields that parseError reads from a PostgREST body and from any other body.
func TestParseError(t *testing.T) {
	tests := []struct {
		name   string
		status int
		raw    string
		want   Error
		kind   error
	}{
		{
			name:   "a PostgREST body with every field",
			status: http.StatusConflict,
			raw:    `{"code":"23505","message":"duplicate key","details":"Key (name)=(a) exists.","hint":"use another name"}`,
			want:   Error{Status: 409, Code: "23505", Message: "duplicate key", Details: "Key (name)=(a) exists.", Hint: "use another name"},
			kind:   ErrConflict,
		},
		{
			name:   "a PostgREST body with null details and hint",
			status: http.StatusBadRequest,
			raw:    `{"code":"PGRST102","message":"empty body","details":null,"hint":null}`,
			want:   Error{Status: 400, Code: "PGRST102", Message: "empty body"},
			kind:   ErrInvalid,
		},
		{
			name:   "a body with a message and no code",
			status: http.StatusNotFound,
			raw:    `{"message":"no such table"}`,
			want:   Error{Status: 404, Message: "no such table"},
			kind:   ErrNotFound,
		},
		{
			name:   "a text body keeps the trimmed text",
			status: http.StatusBadGateway,
			raw:    "  upstream down\n",
			want:   Error{Status: 502, Message: "upstream down"},
		},
		{
			name:   "a JSON body with no code and no message",
			status: http.StatusInternalServerError,
			raw:    `{"other":1}`,
			want:   Error{Status: 500, Message: `{"other":1}`},
		},
		{
			name:   "an empty body",
			status: http.StatusUnauthorized,
			want:   Error{Status: 401},
			kind:   ErrUnauthorized,
		},
		{
			name:   "SQLSTATE 42501 beats HTTP 401",
			status: http.StatusUnauthorized,
			raw:    `{"code":"42501","message":"permission denied"}`,
			want:   Error{Status: 401, Code: "42501", Message: "permission denied"},
			kind:   ErrForbidden,
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			got := parseError(testCase.status, []byte(testCase.raw))

			if got.Status != testCase.want.Status || got.Code != testCase.want.Code || got.Message != testCase.want.Message ||
				got.Details != testCase.want.Details || got.Hint != testCase.want.Hint {
				t.Errorf("parseError() = %+v, want %+v", *got, testCase.want)
			}

			if !errors.Is(got, testCase.kind) && testCase.kind != nil {
				t.Errorf("errors.Is(%v, %v) = false, want true", got, testCase.kind)
			}

			if testCase.kind == nil && got.Unwrap() != nil {
				t.Errorf("Unwrap() = %v, want no kind", got.Unwrap())
			}
		})
	}
}

// TestErrorText checks the text of an Error with each optional part set and unset.
func TestErrorText(t *testing.T) {
	tests := []struct {
		name string
		err  Error
		want string
	}{
		{name: "the status only", err: Error{Status: 500}, want: "postgrest: HTTP 500"},
		{name: "a code", err: Error{Status: 400, Code: "23514"}, want: "postgrest: HTTP 400 23514"},
		{name: "a message", err: Error{Status: 404, Message: "gone"}, want: "postgrest: HTTP 404: gone"},
		{
			name: "every part",
			err:  Error{Status: 409, Code: "23505", Message: "duplicate", Details: "Key exists.", Hint: "rename"},
			want: "postgrest: HTTP 409 23505: duplicate (details: Key exists.) (hint: rename)",
		},
	}

	for _, testCase := range tests {
		t.Run(testCase.name, func(t *testing.T) {
			if got := testCase.err.Error(); got != testCase.want {
				t.Errorf("Error() = %q, want %q", got, testCase.want)
			}
		})
	}
}
