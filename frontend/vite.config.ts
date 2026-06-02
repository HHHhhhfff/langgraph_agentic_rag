import { defineConfig } from 'vite'
import path from 'path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'


function figmaAssetResolver() {
  return {
    name: 'figma-asset-resolver',
    resolveId(id) {
      if (id.startsWith('figma:asset/')) {
        const filename = id.replace('figma:asset/', '')
        return path.resolve(__dirname, 'src/assets', filename)
      }
    },
  }
}

export default defineConfig({
  plugins: [
    figmaAssetResolver(),
    // React 与 Tailwind 插件用于渲染参考设计生成的界面。
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      // 将 @ 指向 src 目录。
      '@': path.resolve(__dirname, './src'),
    },
  },

  // 支持以资源方式导入的文件类型，不要在这里加入 .css、.tsx 或 .ts。
  assetsInclude: ['**/*.svg', '**/*.csv'],
})
