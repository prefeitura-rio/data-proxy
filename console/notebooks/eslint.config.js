import js from '@eslint/js';
import { defineConfig, globalIgnores } from 'eslint/config';
import globals from 'globals';
import ts from 'typescript-eslint';

export default defineConfig(
	globalIgnores(['dist/', 'dist-check/', 'site/', 'test-results/', '.venv/', 'node_modules/']),
	js.configs.recommended,
	ts.configs.strictTypeChecked,
	ts.configs.stylisticTypeChecked,
	{
		languageOptions: {
			globals: { ...globals.browser, ...globals.node },
			parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname }
		},
		rules: {
			eqeqeq: 'error',
			'@typescript-eslint/no-explicit-any': 'error',
			'@typescript-eslint/consistent-type-definitions': ['error', 'type'],
			'@typescript-eslint/no-non-null-assertion': 'error',
			'@typescript-eslint/consistent-type-assertions': ['error', { assertionStyle: 'never' }],
			'@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }]
		}
	},
	{
		// Window is an interface in the DOM types, so the test can only add its fields with an interface.
		files: ['tests/globals.d.ts'],
		rules: { '@typescript-eslint/consistent-type-definitions': 'off' }
	},
	{
		files: ['**/*.js'],
		extends: [ts.configs.disableTypeChecked]
	}
);
