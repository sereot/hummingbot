# Setting Up Hummingbot Development on a New Machine

This guide helps you seamlessly continue Hummingbot development on a new machine with Cursor + Claude Code + Git.

## 1. Prerequisites on New Machine

### Install Required Software

```bash
# Git
sudo apt update && sudo apt install git

# Python 3.9+ (if not already installed)
sudo apt install python3 python3-pip python3-venv

# Build dependencies for Hummingbot
sudo apt install build-essential python3-dev

# Conda (recommended for Hummingbot)
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
```

### Install Development Tools
- **Cursor**: Download from https://cursor.sh/
- **Claude Desktop**: Install Claude for desktop if available
- **VS Code** (optional): As an alternative or backup

## 2. Clone and Set Up Repository

```bash
# Clone your fork
git clone https://github.com/sereot/hummingbot.git
cd hummingbot

# Add upstream remote
git remote add upstream https://github.com/hummingbot/hummingbot.git

# Verify remotes
git remote -v
```

## 3. Set Up Development Environment

```bash
# Create conda environment (matching your current setup)
conda create -n hummingbot python=3.12 -y
conda activate hummingbot

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Compile Cython modules
./compile

# Create necessary directories
mkdir -p logs conf/connectors conf/strategies
```

## 4. Transfer Configuration Files

### Option A: Manual Transfer (Recommended for sensitive data)

```bash
# On OLD machine - create a config bundle (excluding sensitive data)
cd ~/hummingbot-private/hummingbot
tar -czf ~/hummingbot-configs.tar.gz \
  conf/strategies/conf_pure_mm_*.yml \
  docs/sessions/ \
  CLAUDE.md \
  test_credentials.txt

# Transfer to new machine via secure method (SCP, USB, etc.)
scp ~/hummingbot-configs.tar.gz user@newmachine:~/

# On NEW machine - extract
cd ~/hummingbot
tar -xzf ~/hummingbot-configs.tar.gz
```

### Option B: Use Private Git Repo for Configs (Alternative)

```bash
# Create a separate private repo for configs
git init hummingbot-configs
cd hummingbot-configs
cp -r ../hummingbot/conf/strategies .
cp -r ../hummingbot/docs/sessions .
git add . && git commit -m "Initial configs"
git remote add origin https://github.com/yourusername/hummingbot-configs-private.git
git push -u origin master
```

## 5. Configure Git

```bash
# Set up your git identity
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# Set up GPG signing (optional but recommended)
git config --global commit.gpgsign true

# Configure git to handle line endings properly
git config --global core.autocrlf input
```

## 6. Set Up Cursor with Claude

1. Open Cursor on the new machine
2. Sign in to your Cursor account
3. Configure Claude integration:
   - Open Settings (Ctrl+,)
   - Search for "Claude"
   - Add your Claude API key if using API
   - Or configure Claude Desktop integration

## 7. Restore VALR Credentials

```bash
# Create connectors config directory
mkdir -p conf/connectors

# Create valr_hft.yml (you'll need to add your API credentials)
cat > conf/connectors/valr_hft.yml << 'EOF'
valr_api_key: YOUR_API_KEY_HERE
valr_secret_key: YOUR_SECRET_KEY_HERE
EOF

# Set proper permissions
chmod 600 conf/connectors/valr_hft.yml
```

## 8. Verify Everything Works

```bash
# Activate environment
conda activate hummingbot

# Run compilation
./compile

# Test import
python -c "from hummingbot.connector.exchange.valr import ValrExchange; print('✓ VALR connector imports successfully')"

# Start Hummingbot
./start

# In Hummingbot, test connection
>>> connect valr_hft
>>> balance
```

## 9. Sync Latest Changes

```bash
# Fetch latest changes
git fetch origin
git pull origin master

# Check current status
git log --oneline -n 10
git status
```

## 10. Quick Reference for Continuing Work

Create a quick reference script:

```bash
cat > ~/start-hummingbot-dev.sh << 'EOF'
#!/bin/bash
cd ~/hummingbot
conda activate hummingbot
echo "Recent commits:"
git log --oneline -n 5
echo ""
echo "Current branch: $(git branch --show-current)"
echo "Status: $(git status --porcelain | wc -l) uncommitted changes"
echo ""
echo "To start Hummingbot: ./start"
echo "To run tests: pytest tests/"
echo "To check logs: tail -f logs/logs_*.log"
EOF

chmod +x ~/start-hummingbot-dev.sh
```

## 11. Important Files to Check

Make sure these files exist on the new machine:
- `CLAUDE.md` - Project instructions for Claude
- `docs/sessions/*.md` - Session documentation
- `conf/strategies/conf_pure_mm_8.yml` - Your working strategy
- `test_credentials.txt` - VALR test credentials (if needed)

## 12. Cursor Tips for Seamless Transition

1. **Sync Settings**: Cursor can sync settings across machines if you're logged in
2. **Install Extensions**: Make sure Python, GitLens extensions are installed
3. **Configure Workspace**: Open the hummingbot folder as a workspace
4. **Set Python Interpreter**: Select the conda environment (`Ctrl+Shift+P` → "Python: Select Interpreter")

## Quick Checklist

- [ ] Git installed and configured
- [ ] Repository cloned
- [ ] Conda environment created
- [ ] Dependencies installed
- [ ] Cython compiled
- [ ] Config files transferred
- [ ] VALR credentials set up
- [ ] Cursor + Claude configured
- [ ] Test import successful
- [ ] Can run Hummingbot

## Troubleshooting

### Common Issues

1. **Import errors**: Make sure you've run `./compile` after cloning
2. **Missing configs**: Check if config files were properly transferred
3. **API errors**: Verify VALR credentials are correctly set in `conf/connectors/valr_hft.yml`
4. **Git issues**: Ensure you're on the correct branch and have latest changes

### Useful Commands

```bash
# Check Python version
python --version

# Check conda environment
conda info --envs

# Rebuild everything
./clean && ./compile

# Check VALR connector specifically
python -c "import hummingbot.connector.exchange.valr; print('OK')"
```

## Notes

- Keep sensitive data (API keys, credentials) secure and never commit them
- Use `.gitignore` to exclude local config files
- Regularly sync with upstream to get latest Hummingbot updates
- Document your work in session notes for continuity

This setup will give you an identical development environment on your new machine!