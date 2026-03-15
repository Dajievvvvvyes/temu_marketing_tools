# 在 temu_marketing_tools 目录下执行
# 将 YOUR_USERNAME/YOUR_REPO 替换为你的 GitHub 用户名和仓库名

$repoUrl = "https://github.com/YOUR_USERNAME/YOUR_REPO.git"  # 改成你的仓库地址

Set-Location $PSScriptRoot

git init
git add .
git commit -m "Initial commit: temu_marketing_tools"
git branch -M main
git remote add origin $repoUrl
git push -u origin main
