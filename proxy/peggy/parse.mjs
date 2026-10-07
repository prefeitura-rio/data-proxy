import parser from "../parser.ts";

const query = process.argv[2] || "";
const filter = parser.parseFilter(query);

if (filter !== undefined) {
    console.log(JSON.stringify(filter));
}
