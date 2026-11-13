import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  base: "./",
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: Number(process.env.VITE_PORT || 5174),
    strictPort: true,
    fs: { allow: [".."] }
  },
  build: {
    outDir: "dist"
  }
});
