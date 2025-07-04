#!/bin/bash
# Sync with upstream Hummingbot repository

echo "🔄 Syncing with upstream Hummingbot repository..."

# Stash any local changes
if [ -n "$(git status --porcelain)" ]; then
    echo "📦 Stashing local changes..."
    git stash
    STASHED=true
else
    STASHED=false
fi

# Fetch upstream changes
echo "📥 Fetching upstream changes..."
git fetch upstream

# Switch to master branch
echo "🔀 Switching to master branch..."
git checkout master

# Merge upstream changes
echo "🔗 Merging upstream changes..."
git merge upstream/master

# Push to your fork
echo "⬆️ Pushing to your fork..."
git push origin master

# Switch back to development branch if it exists
if git branch | grep -q "development"; then
    echo "🔀 Switching to development branch..."
    git checkout development
    echo "🔗 Merging master into development..."
    git merge master
    echo "⬆️ Pushing development branch..."
    git push origin development
fi

# Restore stashed changes if any
if [ "$STASHED" = true ]; then
    echo "📦 Restoring stashed changes..."
    git stash pop
fi

echo "✅ Sync completed successfully!"
echo "📊 Current branch: $(git branch --show-current)"
echo "📈 Latest commits:"
git log --oneline -5