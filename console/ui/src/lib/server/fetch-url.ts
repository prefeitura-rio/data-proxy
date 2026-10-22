export const urlOf = (input: RequestInfo | URL) => (input instanceof Request ? input.url : input.toString());
