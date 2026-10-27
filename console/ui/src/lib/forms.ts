export const text = (form: FormData, name: string) => {
	const value = form.get(name);

	return typeof value === 'string' ? value.trim() : '';
};

export const optional = (form: FormData, name: string) => text(form, name) || null;

export const whole = (form: FormData, name: string) => {
	const value = optional(form, name);

	return value !== null && /^\d+$/.test(value) ? Number(value) : value;
};

export const list = (form: FormData, name: string) => {
	const items = text(form, name)
		.split(',')
		.map((item) => item.trim())
		.filter(Boolean);

	return items.length > 0 ? items : null;
};
