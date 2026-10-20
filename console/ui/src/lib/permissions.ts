export type Can = { create: boolean; edit: boolean; delete: boolean };

type Tab = {
	id: 'sync' | 'config' | 'access' | 'query';
	label: string;
	href: '/sync' | '/config' | '/access' | '/query';
	needs: string;
};

const TABS: Tab[] = [
	{ id: 'sync', label: 'Sync', href: '/sync', needs: 'read_sync' },
	{ id: 'config', label: 'Config', href: '/config', needs: 'read_config' },
	{ id: 'access', label: 'Access', href: '/access', needs: 'read_policy' },
	{ id: 'query', label: 'Query', href: '/query', needs: 'read_files' }
];

export const can = (permissions: string[], permission: string) => permissions.includes(permission);

export const tabsFor = (permissions: string[]) => TABS.filter((tab) => can(permissions, tab.needs));
