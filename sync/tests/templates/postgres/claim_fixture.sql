{#
{
  "kind": "template",
  "description": "Add one schema, s, with the claim cpf, and one table of it, p.d.t, to the console tables."
}
#}
INSERT INTO console.schemas (name, claim) VALUES ('s', 'cpf');
INSERT INTO console.tables (name, schema) VALUES ('p.d.t', 's');
