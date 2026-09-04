import io
lines = io.open('backtest/config.py', encoding='utf-8').read().split('\n')
start = next(i for i, l in enumerate(lines) if l.startswith('<<<<<<<'))
mid = next(i for i, l in enumerate(lines) if l.startswith('======='))
end = next(i for i, l in enumerate(lines) if l.startswith('>>>>>>>'))
io.open('tests/_head_side.py', 'w', encoding='utf-8').write('\n'.join(lines[start+1:mid]))
io.open('tests/_mine_side.py', 'w', encoding='utf-8').write('\n'.join(lines[mid+1:end]))
print('sides:', mid-start-1, end-mid-1)
