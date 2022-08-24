import os
import glob
from subprocess import run, DEVNULL

"""
Quick script to batch convert flac files in different folders to ogg files
that can be stored with the same directory tree pattern in a different folder

The input directory is a base directory which can have however many sub-directories
The output directory is where you want the output to go.

My music files are stored as ../music/Artist/Album/track.flac
The converted files will be saved as ../music_new/Artist/Album/track.ogg

If you want to save them in the same directory, make the flac and ogg directories 
    in the function the same.

If the folder that has songs also has cover art, it will be copied as well.

A note on ffmpeg: it's quite verbose and outputs to stderr. If you'd like to see 
    all the output, remove the `stderr=DEVNULL` option from the 'run(...)' call 

Any files that couldn't be encoded will be listed in `../ogg_music/encoding_errors.txt`

This script was used on linux (Ubuntu 20.04)
"""

def convert_flac_to_ogg(flac_music, ogg_music):

    music = glob.glob(os.path.join(flac_music, '**', '*.flac'), recursive=True)
    print('total flac files: ', len(music))

    processed_count = 0
    previous_album = None  # for folder
    error_encoding = []

    for current_count, flac_path in enumerate(music):

        percent_done = 100 * current_count // len(music)
        if percent_done > processed_count:
            print(f'Percent of files done: {percent_done}%')
            processed_count = percent_done

        song_flac = os.path.basename(flac_path)
        old_album = os.path.dirname(flac_path)
        if old_album != previous_album:
            album_name = os.path.basename(old_album)
            print(album_name)

        new_album = old_album.replace(flac_music, ogg_music)
        # print(new_album)

        ogg_path = os.path.join(new_album, song_flac).replace('.flac', '.ogg')
        # print(ogg_path)

        if not os.path.isdir(new_album):
            os.makedirs(new_album)

        # explanation of the ffmpeg command:  `ffmpeg -i flac_path -y -c:a libopus -ab 256k -vn ogg_path`
        #   -i is for input file, flac_path in this case
        #   -y is to overwrite output without asking
        #   -c:a selects output stream, audio only in this case, I want 'libopus'
        #   -ab sets bitrate, I set it for 256k
        #   -vn stops a low-resolution cover image (theora format) from being embedded into the .ogg container


        if not os.path.isfile(ogg_path):
            result = run(["ffmpeg", "-i", flac_path, "-y", "-c:a", "libopus", "-ab", "256k", "-vn", ogg_path],
                         stderr=DEVNULL)
            if result.returncode != 0:
                error_encoding.append(song_flac)

        if old_album != previous_album:

            images = []
            for imgtype in ['jpg', 'JPG', 'jpeg', 'JPEG', 'png', 'PNG']:
                images += glob.glob(os.path.join(old_album, '*.' + imgtype))

            for cover in images:
                new_cover_path = cover.replace(old_album, new_album)
                if not os.path.isfile(new_cover_path):
                    # print(new_cover_path)
                    run(['cp', cover, new_cover_path])

        previous_album = old_album

    fid = open(os.path.join(ogg_music, 'encoding_errors.txt'), 'a')
    for item in error_encoding:
        fid.write(item+'\n')
    fid.close()

    return error_encoding


if __name__ == "__main__":

    flac_dir = '/khazaddum/alanine/music/all_music'
    ogg_dir = '/khazaddum/alanine/music/music_mp3'
    errors = convert_flac_to_ogg(flac_dir, ogg_dir)
