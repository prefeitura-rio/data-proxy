use ./lib.nu [wait-for-postgres]

# Wait until PostgreSQL accepts connections.
def main []: nothing -> nothing {
    wait-for-postgres
}
