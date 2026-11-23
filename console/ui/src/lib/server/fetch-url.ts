export const requestUrl = (input: RequestInfo | URL) => (input instanceof Request ? input.url : input.toString());
