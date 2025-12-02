TODO2: create phylogenetic tree of vimrcs
TODO3: add all options from option.c (see src/nvim/option.c)
TODO4: cover the entire github periodically

This script scrapes vimrc's hosted in github and returns the most commonly used vim configurations.

```python eigenvimrc.py```

The "voting" process may iterate as follows:

```python
def vote(default):
    vimrcs = people_curate_their_vimrc()
    sleep(a_month)
    new_default = most_common_50_percent(vimrcs)
    if new_default != default:
        return vote(new_default)
    else:
        return new_default
```

#Install
3. Make sure pathogen is available and ```execute pathogen#infect()``` is in ```~/.vimrc```
2. ```cd ~/.vim/bundle```
3. ```git clone git://github.com/rht/eigenvimrc.git```

#Result
```set nocompatible``` > ```syntax on```


