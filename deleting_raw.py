import os
import os.path
dir_del = "/path/to/folder"

if dir_del[-1] != '/':
    dir_del = dir_del + '/'

list_files = [i for i in os.listdir(dir_del) if '.CR2' in i]
for i in list_files:
    jpg = dir_del+i[:-3]+'JPG'
    if not(os.path.isfile(jpg)):
        print('Deleting:'+i)
        os.remove(dir_del+i)
        
#os.getcwd()
