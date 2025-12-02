

#Plot
Strangely it doesn't follow the power law distribution. Likely because some settings are highly correlated with the others.
![plot](fig.png)

#Data
Repository list is queried from [http://ghtorrent.org/dblite/](http://ghtorrent.org/dblite/)

```SELECT * FROM projects WHERE language = 'VimL' AND ((name = 'dotfiles') OR (name = 'vimrc'))```
