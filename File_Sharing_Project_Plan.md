# Concurrent File Sharing System
## Project Plan and Task Split

## What We're Building

A file-sharing system where many users can upload and download files from a server at the same time, using TCP sockets. On top of that, the project will add:

- Login and permissions (who can access which file)
- File details (owner, size, type, upload time)
- SDN rules that allow or block clients on the network
- Bandwidth and traffic monitoring
- Testing with several transfers running together

A UI is optional. The core system will be completed first, and a simple dashboard may be added if time allows.

## How It Fits Together

```text
SDN Controller (access rules + monitoring)
                  |
              SDN Switch
                  |
       Client 1, Client 2, Client 3
                  |
              TCP Server
       (handles many connections)
                  |
          File Storage + Database
```

## Task Split: 3 Modules

Pick whichever module you like; the tasks can then be broken into smaller steps.

| Module | What You Own |
|---|---|
| **1. TCP + File Transfer** | Server, client, upload, download, and handling many users at once |
| **2. Login + File Management** | Login, permissions, file details, and database |
| **3. SDN + Monitoring** | Network setup, controller, access rules, and bandwidth tracking |

## Module 1: TCP + File Transfer

**Goal:** Get the file sharing itself working.

- TCP server and client, plus the message format they use
- Upload, download, and list files
- Handle multiple clients at once (threads or multiprocessing)
- Handle connection and transfer errors
- Test simultaneous uploads and downloads

## Module 2: Login + File Management

**Goal:** Manage users, files, and permissions.

- Register and log in with username and password
- Reject users who aren't logged in
- Permissions: who can upload, who can download, and who owns which file
- Store file details: name, owner, size, type, and upload time
- Database for users, files, and permissions (SQLite or JSON; decide together)

## Module 3: SDN + Monitoring

**Goal:** Add network control and traffic tracking.

- Set up the network with Mininet: topology, SDN switch, and controller
- Connect clients and server through it
- Access rules: authorized client is allowed; unauthorized client is blocked
- Track active connections, bytes transferred, transfer time, and bandwidth

## How We'll Work

1. Agree on the basics before anyone codes: programming language, message format between client and server, folder structure, database, SDN tools, and how the modules communicate.
2. Build modules separately, using Git/GitHub.
3. Join Module 1 and Module 2, then test that logged-in users can upload and download.
4. Run the working system inside the SDN network and add the access rules.
5. Add monitoring (connections, transfer size, duration, and bandwidth).
6. Test everything together (see the list below).
7. Optional: add a simple UI for login, upload, download, my files, and network stats.

## Tests to Run

1. Single-client upload
2. Single-client download
3. Multiple clients connected
4. Simultaneous uploads
5. Simultaneous downloads
6. Upload and download at the same time
7. Wrong login details
8. Access to someone else's file
9. Blocked client on the network
10. Bandwidth monitoring

## Final Demo Flow

1. Start the SDN network and the server.
2. Start three clients and log in.
3. Client 1 uploads, Client 2 downloads, and Client 3 uploads, all at the same time.
4. Show the server's transfer information and the controller's access rules.
5. Show bandwidth and traffic statistics.
6. Try unauthorized access and show that it is blocked.

## Important

Everyone should understand the full project, not just their own module, because everyone will have to explain it in the demo/viva.
