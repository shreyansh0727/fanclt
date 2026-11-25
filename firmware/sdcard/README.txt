FanCtl voice clips
==================

The ESP32 controller drives a DFPlayer Mini (MP3-TF-16P). The clips in the
mp3/ folder are the words the speaker can say.

Copy the whole mp3 folder to the root of a FAT32 microSD card (32 GB or
smaller) so the module sees /mp3/0001.mp3 ... /mp3/0059.mp3. DFPlayer needs
four digit file names; the folder name must be lowercase "mp3".

Track table
-----------
0001  pairing code is            0016-0025  zero .. nine
0002  connected to wifi          0026-0051  A .. Z as "letter, NATO word"
0003  connect to hotspot         0052       cat meow (generated, not speech)
0004  then open 192.168.4.1      0053       purr purr
0005  setup mode                 0054       yum yum
0006  paired                     0055       let's play
0007  ready                      0056       good night
0008  channel                    0057       good morning
0009  all                        0058       alarm siren (generated)
0010  on                         0059       reminder chime (generated)
0011  off
0012  fan
0013  light
0014  socket
0015  here i am

Regenerate with: .venv/Scripts/python.exe scripts/make_voice_clips.py
Just the meow:   .venv/Scripts/python.exe scripts/make_voice_clips.py --tracks 52
Just the chimes: .venv/Scripts/python.exe scripts/make_voice_clips.py --tracks 58,59
