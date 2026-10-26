/** The name of a table in the notebook: the part after the last dot. The grant and the notebook use the same rule. */
export const destination = (table: string): string => table.replace(/^.*\./, '');
