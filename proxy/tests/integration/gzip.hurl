# A client that accepts gzip gets a JSON body compressed. Hurl decodes the body, so the
# assert checks that the decoded text is the whole answer.
GET http://{{host}}/big
Accept-Encoding: gzip

HTTP 200
Content-Encoding: gzip
Vary: Accept-Encoding
[Asserts]
bytes count == 300002

# A client that does not ask for gzip gets the plain body.
GET http://{{host}}/big

HTTP 200
[Asserts]
header "Content-Encoding" not exists
bytes count > 300000
