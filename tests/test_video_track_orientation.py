# SPDX-License-Identifier: GPL-2.0-or-later
import io,struct,tempfile,unittest
from pathlib import Path
from video_track_orientation import inspect_stream,inspect_file

IDENTITY=(65536,0,0,0,65536,0,0,0,1073741824)
ROT90=(0,-65536,0,65536,0,0,0,0,1073741824)
ROT270=(0,65536,0,-65536,0,0,0,0,1073741824)

def box(tag,payload=b'',extended=False):
    return (struct.pack('>I4sQ',1,tag,len(payload)+16) if extended else struct.pack('>I4s',len(payload)+8,tag))+payload

def track(ident,handler,matrix,version=0):
    data=bytearray(84+12*version);data[0]=version
    struct.pack_into('>I',data,12+8*version,ident)
    struct.pack_into('>9i',data,40+12*version,*matrix)
    struct.pack_into('>2I',data,76+12*version,1920<<16,1080<<16)
    return box(b'trak',box(b'tkhd',data)+box(b'mdia',box(b'hdlr',bytes(8)+handler+bytes(12))))

def movie(*tracks):
    header=bytearray(100);struct.pack_into('>9i',header,36,*IDENTITY)
    return box(b'ftyp',b'qt  '+bytes(4))+box(b'moov',box(b'mvhd',header)+b''.join(tracks),True)


class OrientationInspection(unittest.TestCase):
    def test_video_audio_and_movie_matrices_are_separate(self):
        raw=movie(track(1,b'vide',ROT90),track(2,b'soun',IDENTITY),track(3,b'vide',ROT270,1))
        result=inspect_stream(io.BytesIO(raw),len(raw))
        self.assertEqual(result['movie_matrices'][0]['words'],list(IDENTITY))
        self.assertEqual([r['handler'] for r in result['tracks']],['vide','soun','vide'])
        self.assertEqual([r['matrix']['canonical_rotation_degrees'] for r in result['tracks']],[90,0,270])
        self.assertEqual([r['track_id'] for r in result['tracks']],[1,2,3])
        self.assertEqual(result['tracks'][2]['width_fixed16'],1920<<16)

    def test_read_only_file_and_mdat_is_not_scanned_for_fake_atoms(self):
        raw=movie(track(1,b'vide',IDENTITY))+box(b'mdat',b'moovtkhdvide'+bytes(4096))
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'original.mov';path.write_bytes(raw)
            self.assertEqual(inspect_file(path)['video_track_count'],1)
            self.assertEqual(path.read_bytes(),raw)

    def test_large_media_payload_is_skipped(self):
        class BoundedReader(io.BytesIO):
            def read(self,n=-1):
                if n<0 or n>128:raise AssertionError('Read encoded payload')
                return super().read(n)
        raw=movie(track(1,b'vide',IDENTITY))+struct.pack('>I4s',0,b'mdat')+bytes(10000)
        self.assertEqual(inspect_stream(BoundedReader(raw),len(raw))['video_track_count'],1)

    def test_malformed_boxes_and_unsupported_versions_fail(self):
        examples=(bytes(7),struct.pack('>I4s',4,b'moov'),struct.pack('>I4s',100,b'moov'),
                  struct.pack('>I4s',1,b'moov'),box(b'moov',box(b'trak',box(b'tkhd',bytes([2])+bytes(83)))),
                  movie(track(1,b'vide',IDENTITY))+b'x',box(b'mdat',b'only media'))
        for raw in examples:
            with self.subTest(raw=raw[:16]),self.assertRaises(ValueError):inspect_stream(io.BytesIO(raw),len(raw))

    def test_translation_preserved_and_reflection_not_called_rotation(self):
        translated=list(ROT90);translated[6]=1080<<16
        mirrored=list(IDENTITY);mirrored[0]=-65536
        raw=movie(track(1,b'vide',translated),track(2,b'vide',mirrored))
        result=inspect_stream(io.BytesIO(raw),len(raw))
        self.assertEqual(result['tracks'][0]['matrix']['words'][6],1080<<16)
        self.assertEqual(result['tracks'][0]['matrix']['canonical_rotation_degrees'],90)
        self.assertIsNone(result['tracks'][1]['matrix']['canonical_rotation_degrees'])


if __name__=='__main__':unittest.main()
