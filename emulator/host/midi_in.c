/* MIDI input shared by the device engines: CoreMIDI sources (a KeyStep, a DAW's virtual port...)
 * into the emulated machine's MIDI IN through dt_rt_midi, as the plug-ins get MIDI from their host.
 * All events come from CoreMIDI's one callback thread, which is what dt_rt_midi's single-producer
 * ring needs. Compiled into each device's host library against that engine's dtcore.h. */
#include "dtcore.h"

#include <CoreMIDI/CoreMIDI.h>
#include <stdio.h>
#include <string.h>

static MIDIClientRef g_client;
static MIDIPortRef g_port;
static dt_rt *g_midi_rt;
static uint64_t g_connected;                     /* bit i: source i is connected */
static volatile uint32_t g_msgs;                  /* events passed to the machine */
static volatile uint8_t g_last[3];                /* the last channel message, for the UI */

static void fc_midi_read(const MIDIPacketList *list, void *ref, void *src)
{
    const MIDIPacket *p = &list->packet[0];
    UInt32 i;
    (void)ref; (void)src;
    for (i = 0; i < list->numPackets; i++) {
        UInt16 off = 0;
        while (off < p->length && g_midi_rt) {
            UInt16 n = p->length - off > 250 ? 250 : p->length - off;
            if (p->data[off] == 0xFE) {          /* active sensing: noise for the machine */
                off++;
                continue;
            }
            dt_rt_midi(g_midi_rt, 0, p->data + off, n);
            if (p->data[off] >= 0x80 && p->data[off] < 0xF0) {
                g_last[0] = p->data[off];
                g_last[1] = n > 1 ? p->data[off + 1] : 0;
                g_last[2] = n > 2 ? p->data[off + 2] : 0;
            }
            g_msgs++;
            off += n;
        }
        p = MIDIPacketNext(p);
    }
}

/* number of MIDI sources now; names via fc_midi_name */
int fc_midi_count(void)
{
    return (int)MIDIGetNumberOfSources();
}

int fc_midi_name(int i, char *out, size_t n)
{
    CFStringRef s = NULL;
    MIDIEndpointRef e = MIDIGetSource((ItemCount)i);
    out[0] = 0;
    if (!e || MIDIObjectGetStringProperty(e, kMIDIPropertyDisplayName, &s) || !s)
        return -1;
    CFStringGetCString(s, out, (CFIndex)n, kCFStringEncodingUTF8);
    CFRelease(s);
    return 0;
}

/* connect exactly the sources whose bit is set in `mask` (bit i = source i); 0 = ok */
int fc_midi_select(dt_rt *rt, uint64_t mask, char *err, size_t errlen)
{
    int i, n;
    if (!g_client) {
        if (MIDIClientCreate(CFSTR("digimod"), NULL, NULL, &g_client)
            || MIDIInputPortCreate(g_client, CFSTR("in"), fc_midi_read, NULL, &g_port)) {
            snprintf(err, errlen, "CoreMIDI unavailable");
            return -1;
        }
    }
    g_midi_rt = rt;
    n = fc_midi_count();
    for (i = 0; i < n && i < 64; i++) {
        MIDIEndpointRef e = MIDIGetSource((ItemCount)i);
        int want = (int)((mask >> i) & 1), have = (int)((g_connected >> i) & 1);
        if (want && !have && MIDIPortConnectSource(g_port, e, NULL) == noErr)
            g_connected |= 1ull << i;
        else if (!want && have && MIDIPortDisconnectSource(g_port, e) == noErr)
            g_connected &= ~(1ull << i);
    }
    return 0;
}

uint64_t fc_midi_connected(void)
{
    return g_connected;
}

/* events passed so far and the last channel message (status, data1, data2) */
uint32_t fc_midi_stats(uint8_t last[3])
{
    last[0] = g_last[0];
    last[1] = g_last[1];
    last[2] = g_last[2];
    return g_msgs;
}
