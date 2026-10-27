const FIELD = /^[0-9A-Za-z*/,?#-]+$/;

/** The check of `console.set_schedule`: 5 fields, or 6 with the seconds first, separated by one space. */
export const isCron = (text: string): boolean => {
	const fields = text.split(' ');

	return (fields.length === 5 || fields.length === 6) && fields.every((field) => FIELD.test(field));
};
