import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "dist.next", "node_modules"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    plugins: { "react-hooks": reactHooks },
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    rules: {
      // The two classic hook rules only: this code keeps "latest value" refs on purpose, which the
      // React Compiler rules in the plugin's recommended set would reject.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "@typescript-eslint/no-non-null-assertion": "off",
      // Full-width spaces are ordinary punctuation in Chinese text.
      "no-irregular-whitespace": ["error", { skipJSXText: true, skipStrings: true, skipTemplates: true }],
    },
  },
);
